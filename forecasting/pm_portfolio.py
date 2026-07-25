"""Paper-trading portfolio for Polymarket KPI forecasts. Parallel to portfolio.py.

Same max-divergence rule as the Kalshi book, extended to binary earnings
beats/misses (one contract, edge = |our P - market P|). Separate ledger at
data/pm_portfolio.json so the two venues run fully in parallel.

`init` / `add` open positions; `mark` settles via the Gamma API (a market is
resolved when it is closed and its outcome prices collapse to 0/1).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from market_lookup.providers.common import get_json, parse_jsonish_list, probability
from forecasting.sources.polymarket import GAMMA, _bucket_range

ROOT = Path(__file__).resolve().parents[1]
OPEN = ROOT / "data" / "polymarket_kpi_open.jsonl"
FCST = ROOT / "data" / "forecasts" / "open_pm_claudeprophet.jsonl"
LEDGER = ROOT / "data" / "pm_portfolio.json"

BANKROLL = 1000.0
MIN_EDGE = 0.05
PRICE_BAND = (0.03, 0.97)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_markets() -> list[dict]:
    return [json.loads(l) for l in OPEN.open()] if OPEN.exists() else []


def load_forecasts() -> list[dict]:
    if not FCST.exists():
        return []
    out = []
    for l in FCST.open():
        r = json.loads(l)
        if "cp_median" in r or "cp_p" in r:
            out.append(r)
    return out


def _best_pick(fc: dict, markets: list[dict]):
    """Return (diff, cp, mid, market_row) for the contract we'd bet, or None."""
    key = (fc["co"], fc["metric"], fc["period"])
    rows = [m for m in markets if (m["company"], m["metric"], m["period"]) == key]
    if fc["kind"] == "binary":
        row = next((m for m in rows if m["outcome_kind"] == "binary"), None)
        mid, cp = (row.get("yes_mid") if row else None), fc.get("cp_p")
        if row is None or mid is None or cp is None or not (PRICE_BAND[0] <= mid <= PRICE_BAND[1]):
            return None
        return (cp - mid, cp, mid, row)
    # ladder: best-edge threshold
    cp_by_thr = {round(t["t"]): t["cp_p"] for t in fc.get("cp_thresholds", [])}
    best = None
    for m in rows:
        thr = m.get("threshold")
        mid = m.get("yes_mid")
        if thr is None or mid is None:
            continue
        cp = cp_by_thr.get(round(thr))
        if cp is None or not (PRICE_BAND[0] <= mid <= PRICE_BAND[1]):
            continue
        diff = cp - mid
        if best is None or abs(diff) > abs(best[0]):
            best = (diff, cp, mid, m)
    return best


def _positions_for(forecasts, markets, stake=None):
    picks = []
    for fc in forecasts:
        best = _best_pick(fc, markets)
        if best is None or abs(best[0]) < MIN_EDGE:
            continue
        picks.append((fc, *best))
    per = stake if stake is not None else (round(BANKROLL / len(picks), 2) if picks else 0.0)
    positions = []
    for fc, diff, cp, mid, m in picks:
        side = "YES" if diff > 0 else "NO"
        entry = mid if side == "YES" else round(1 - mid, 3)
        positions.append({
            "ticker": m.get("ticker"), "co": fc["co"], "metric": fc["metric"],
            "period": fc["period"], "kind": fc["kind"], "threshold": m.get("threshold"),
            "question": m.get("question"), "market_url": m.get("market_url"),
            "side": side, "cp_p": round(cp, 3), "entry_yes_mid": mid, "entry_price": entry,
            "stake": per, "contracts": round(per / entry, 2) if entry else 0.0,
            "entry_date": now_iso()[:10], "source": "polymarket",
            "status": "open", "result": None, "realized_pnl": None,
        })
    return positions


def cmd_init(args) -> int:
    if LEDGER.exists() and not args.force:
        print(f"{LEDGER} exists; use --force to rebuild.")
        return 1
    positions = _positions_for(load_forecasts(), load_markets())
    per = positions[0]["stake"] if positions else 0.0
    LEDGER.write_text(json.dumps({"created": now_iso(), "bankroll": BANKROLL,
                                  "stake_per_position": per, "min_edge": MIN_EDGE,
                                  "positions": positions}, indent=2) + "\n")
    print(f"opened {len(positions)} PM paper positions (${sum(p['stake'] for p in positions):,.0f}) -> {LEDGER}")
    for p in positions:
        print(f"  {p['side']:3} [{p['kind']:6}] {p['co']} — {p['metric'][:28]} @ {p['entry_price']:.2f} "
              f"(our={p['cp_p']:.2f}, mkt={p['entry_yes_mid']:.2f})")
    return 0


def cmd_add(args) -> int:
    ledger = json.loads(LEDGER.read_text())
    have = {(p["co"], p["metric"], p["period"]) for p in ledger["positions"]}
    new_fc = [fc for fc in load_forecasts() if (fc["co"], fc["metric"], fc["period"]) not in have]
    added = _positions_for(new_fc, load_markets(), stake=ledger.get("stake_per_position") or None)
    ledger["positions"].extend(added)
    LEDGER.write_text(json.dumps(ledger, indent=2) + "\n")
    print(f"added {len(added)} PM positions; ledger now {len(ledger['positions'])}")
    for p in added:
        print(f"  {p['side']:3} [{p['kind']:6}] {p['co']} — {p['metric'][:28]} @ {p['entry_price']:.2f}")
    return 0


def _resolved_yes(condition_id: str) -> bool | None:
    """True/False if the market resolved Yes/No, None if still open/unknown."""
    try:
        payload = get_json(f"{GAMMA}/markets", {"condition_ids": condition_id})
    except Exception:
        return None
    mk = (payload[0] if isinstance(payload, list) and payload else None)
    if not mk or not mk.get("closed"):
        return None
    prices = [probability(x) for x in parse_jsonish_list(mk.get("outcomePrices"))]
    if len(prices) >= 2 and prices[0] is not None:
        if prices[0] >= 0.99:
            return True
        if prices[0] <= 0.01:
            return False
    return None


def _resolved_bucket_value_edge(slug: str):
    """For a bucket event, return the lower edge of the winning bucket, or None."""
    try:
        evs = get_json(f"{GAMMA}/events", {"slug": slug})
    except Exception:
        return None
    ev = evs[0] if isinstance(evs, list) and evs else evs
    if not isinstance(ev, dict):
        return None
    for m in ev.get("markets") or []:
        prices = [probability(x) for x in parse_jsonish_list(m.get("outcomePrices"))]
        if prices and prices[0] is not None and prices[0] >= 0.99:
            rng = _bucket_range(m.get("groupItemTitle") or "")
            return rng[0] if rng else None  # lower edge of the winning bucket
    return None


def cmd_mark(args) -> int:
    ledger = json.loads(LEDGER.read_text())
    resolved = 0
    for p in ledger["positions"]:
        if p["status"] != "open":
            continue
        won = None
        if p["kind"] == "bucket_cdf":
            edge = _resolved_bucket_value_edge((p.get("market_url") or "").rsplit("/", 1)[-1])
            if edge is not None and p.get("threshold") is not None:
                yes_outcome = edge >= p["threshold"]          # value >= our ">= threshold"
                won = yes_outcome == (p["side"] == "YES")
        else:
            yes = _resolved_yes(p.get("ticker") or "")
            if yes is not None:
                won = yes == (p["side"] == "YES")
        if won is None:
            continue
        p["status"] = "resolved"
        p["result"] = "yes" if won == (p["side"] == "YES") else "no"
        p["realized_pnl"] = round(p["contracts"] * (1.0 if won else 0.0) - p["stake"], 2)
        p["resolved_date"] = now_iso()[:10]
        resolved += 1
    ledger["marked_at"] = now_iso()
    LEDGER.write_text(json.dumps(ledger, indent=2) + "\n")
    print(f"marked PM ledger: {resolved} newly resolved, "
          f"{sum(1 for p in ledger['positions'] if p['status']=='open')} still open")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="pm_portfolio")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ip = sub.add_parser("init"); ip.add_argument("--force", action="store_true")
    sub.add_parser("add")
    sub.add_parser("mark")
    args = ap.parse_args()
    return {"init": cmd_init, "add": cmd_add, "mark": cmd_mark}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())

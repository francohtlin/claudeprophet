"""Paper-trading portfolio built from ClaudeProphet KPI forecasts.

`init`  — open one paper position per forecasted metric: the contract where our
          probability diverges most from the market mid (min edge required).
          Fixed paper stake per position, entry at the current mid.
`mark`  — refresh: check open positions for resolution (settled markets) and
          record realized P&L; leaves open positions to be marked to market by
          the dashboard from the latest price pull.

Ledger lives at data/portfolio.json. Paper only — nothing is traded.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from forecasting.kpi_metrics import FORECASTS, OPEN_MARKETS, parse

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "data" / "portfolio.json"

BANKROLL = 1000.0      # paper bankroll; split equally across positions at build
MIN_EDGE = 0.05        # only trade contracts where |our_p - market_mid| >= 5 pts
PRICE_BAND = (0.03, 0.97)  # skip near-settled tails


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_open_markets() -> list[dict]:
    return [json.loads(l) for l in OPEN_MARKETS.open()]


def load_forecasts() -> list[dict]:
    return [json.loads(l) for l in FORECASTS.open() if "cp_median" in json.loads(l)]


def contract_rows_for(fc: dict, markets: list[dict]) -> list[dict]:
    """Open contracts belonging to one forecast metric, with parsed thresholds."""
    out = []
    for m in markets:
        co = (m.get("company") or "").replace(" KPI", "").strip()
        if co != fc["co"]:
            continue
        if (m.get("close_time") or "")[:10] != fc["resolves"]:
            continue
        p = parse(m.get("question", ""))
        if not p:
            continue
        val, metric, period = p
        if metric != fc["metric"] or period != fc["period"]:
            continue
        out.append({**m, "threshold": val})
    return out


def _open_positions_for(forecasts: list[dict], markets: list[dict],
                        stake: float | None = None) -> list[dict]:
    """Two-pass: pick the best-edge contract per metric, then size equally
    from BANKROLL unless an explicit per-position stake is given."""
    picks = []
    for fc in forecasts:
        cp_by_thr = {round(t["t"]): t["cp_p"] for t in fc.get("cp_thresholds", [])}
        best = None
        for c in contract_rows_for(fc, markets):
            mid = c.get("yes_mid")
            cp = cp_by_thr.get(round(c["threshold"]))
            if mid is None or cp is None or not (PRICE_BAND[0] <= mid <= PRICE_BAND[1]):
                continue
            diff = cp - mid
            if best is None or abs(diff) > abs(best[0]):
                best = (diff, cp, mid, c)
        if best is None:
            continue
        diff, cp, mid, c = best
        if abs(diff) < MIN_EDGE:
            continue
        picks.append((fc, diff, cp, mid, c))

    per = stake if stake is not None else (round(BANKROLL / len(picks), 2) if picks else 0.0)
    positions = []
    for fc, diff, cp, mid, c in picks:
        side = "YES" if diff > 0 else "NO"
        entry = mid if side == "YES" else round(1 - mid, 3)
        positions.append({
            "ticker": c["ticker"], "series_ticker": c.get("series_ticker"),
            "co": fc["co"], "metric": fc["metric"], "period": fc["period"],
            "resolves": fc["resolves"], "question": c["question"],
            "side": side, "cp_p": cp, "entry_yes_mid": mid, "entry_price": entry,
            "stake": per, "contracts": round(per / entry, 2),
            "entry_date": now_iso()[:10],
            "status": "open", "result": None, "realized_pnl": None,
        })
    return positions


def cmd_init(args) -> int:
    if LEDGER.exists() and not args.force:
        print(f"{LEDGER} exists; use --force to rebuild from scratch.")
        return 1
    positions = _open_positions_for(load_forecasts(), load_open_markets())
    per = positions[0]["stake"] if positions else 0.0
    ledger = {"created": now_iso(), "bankroll": BANKROLL,
              "stake_per_position": per,
              "min_edge": MIN_EDGE, "positions": positions}
    LEDGER.write_text(json.dumps(ledger, indent=2) + "\n")
    dep = sum(p["stake"] for p in positions)
    print(f"opened {len(positions)} paper positions (${dep:,.0f} deployed) -> {LEDGER}")
    return 0


def cmd_add(args) -> int:
    """Open positions only for forecasted metrics not already in the ledger."""
    ledger = json.loads(LEDGER.read_text())
    have = {(p["co"], p["metric"], p["period"], p["resolves"]) for p in ledger["positions"]}
    new_fc = [fc for fc in load_forecasts()
              if (fc["co"], fc["metric"], fc["period"], fc["resolves"]) not in have]
    added = _open_positions_for(new_fc, load_open_markets(),
                                stake=ledger.get("stake_per_position") or None)
    ledger["positions"].extend(added)
    LEDGER.write_text(json.dumps(ledger, indent=2) + "\n")
    print(f"added {len(added)} new positions (from {len(new_fc)} new forecasts); "
          f"ledger now {len(ledger['positions'])} positions")
    for p in added:
        print(f"  {p['side']:3} {p['co']} — {p['metric']} @ {p['entry_price']:.2f} "
              f"(our p={p['cp_p']:.2f}, mkt={p['entry_yes_mid']:.2f})")
    return 0


def cmd_mark(args) -> int:
    from backtest.kalshi import list_markets
    ledger = json.loads(LEDGER.read_text())
    open_pos = [p for p in ledger["positions"] if p["status"] == "open"]
    by_series: dict[str, list[dict]] = {}
    for p in open_pos:
        by_series.setdefault(p["series_ticker"], []).append(p)
    resolved = 0
    for series, plist in by_series.items():
        try:
            settled = {m.get("ticker"): m for m in list_markets(series, status="settled", max_markets=2000)}
        except Exception:
            continue
        for p in plist:
            m = settled.get(p["ticker"])
            if not m:
                continue
            result = str(m.get("result") or "").lower()
            if result not in ("yes", "no"):
                continue
            won = (result == "yes") == (p["side"] == "YES")
            payout = p["contracts"] * (1.0 if won else 0.0)
            p["status"] = "resolved"
            p["result"] = result
            p["realized_pnl"] = round(payout - p["stake"], 2)
            p["resolved_date"] = now_iso()[:10]
            resolved += 1
    ledger["marked_at"] = now_iso()
    LEDGER.write_text(json.dumps(ledger, indent=2) + "\n")
    print(f"marked ledger: {resolved} newly resolved, "
          f"{sum(1 for p in ledger['positions'] if p['status']=='open')} still open")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="portfolio")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ip = sub.add_parser("init"); ip.add_argument("--force", action="store_true")
    sub.add_parser("add")
    sub.add_parser("mark")
    args = ap.parse_args()
    return {"init": cmd_init, "add": cmd_add, "mark": cmd_mark}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())

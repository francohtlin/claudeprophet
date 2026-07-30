"""Flat-$1 paper portfolio for Kalshi weather forecasts. Parallel to
portfolio.py / pm_portfolio.py. Same max-divergence rule; settles via Kalshi
(the market's `result`). Ledger shape matches the other books so the dashboard
reads it with the shared ladder machinery.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from forecasting.weather_tracks import cfg

ROOT = Path(__file__).resolve().parents[1]
# Path defaults (wealthsimple track); main() overrides these from --track.
OPEN = cfg("wealthsimple")["open"]
FCST = cfg("wealthsimple")["fcst"]
LEDGER = cfg("wealthsimple")["ledger"]

STAKE = 1.0
MIN_EDGE = 0.05
PRICE_BAND = (0.03, 0.97)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_markets():
    return [json.loads(l) for l in OPEN.open()] if OPEN.exists() else []


def load_forecasts():
    return [json.loads(l) for l in FCST.open() if "cp_median" in json.loads(l)] if FCST.exists() else []


def _best_pick(fc, markets):
    key = (fc["co"], fc["metric"], fc["period"])
    rows = [m for m in markets if (m["company"], m["metric"], m["period"]) == key]
    cp_by = {round(t["t"]): t["cp_p"] for t in fc.get("cp_thresholds", [])}
    best = None
    for m in rows:
        thr, mid = m.get("threshold"), m.get("yes_mid")
        if thr is None or mid is None:
            continue
        cp = cp_by.get(round(thr))
        if cp is None or not (PRICE_BAND[0] <= mid <= PRICE_BAND[1]):
            continue
        diff = cp - mid
        if best is None or abs(diff) > abs(best[0]):
            best = (diff, cp, mid, m)
    return best


def _positions_for(forecasts, markets):
    out = []
    for fc in forecasts:
        b = _best_pick(fc, markets)
        if b is None or abs(b[0]) < MIN_EDGE:
            continue
        diff, cp, mid, m = b
        side = "YES" if diff > 0 else "NO"
        entry = mid if side == "YES" else round(1 - mid, 3)
        out.append({
            "ticker": m.get("ticker"), "series_ticker": m.get("series_ticker"),
            "co": fc["co"], "metric": fc["metric"], "period": fc["period"],
            "kind": "threshold", "threshold": m.get("threshold"),
            "question": m.get("question"), "market_url": m.get("market_url"),
            "side": side, "cp_p": round(cp, 3), "entry_yes_mid": mid, "entry_price": entry,
            "stake": STAKE, "contracts": round(STAKE / entry, 2) if entry else 0.0,
            "entry_date": now_iso()[:10], "source": "kalshi-weather",
            "status": "open", "result": None, "realized_pnl": None,
        })
    return out


def _rebalance(ledger):
    for p in ledger["positions"]:
        p["stake"] = STAKE
        p["contracts"] = round(STAKE / p["entry_price"], 2) if p.get("entry_price") else 0.0
        if p["status"] == "resolved" and p.get("result") in ("yes", "no"):
            won = (p["result"] == "yes") == (p["side"] == "YES")
            p["realized_pnl"] = round(p["contracts"] * (1.0 if won else 0.0) - STAKE, 2)
    ledger["stake_per_position"] = STAKE


def cmd_init(args):
    if LEDGER.exists() and not args.force:
        print(f"{LEDGER} exists; use --force."); return 1
    positions = _positions_for(load_forecasts(), load_markets())
    LEDGER.write_text(json.dumps({"created": now_iso(), "bankroll": 1000.0,
                                  "stake_per_position": STAKE, "min_edge": MIN_EDGE,
                                  "positions": positions}, indent=2) + "\n")
    print(f"opened {len(positions)} weather paper bets (${len(positions)*STAKE:.0f}) -> {LEDGER}")
    for p in positions:
        print(f"  {p['side']:3} {p['co']} ({p['period']}) >= {p['threshold']:g} @ {p['entry_price']:.2f} "
              f"(our={p['cp_p']:.2f}, mkt={p['entry_yes_mid']:.2f})")
    return 0


def cmd_add(args):
    ledger = json.loads(LEDGER.read_text())
    have = {(p["co"], p["metric"], p["period"]) for p in ledger["positions"]}
    new = [fc for fc in load_forecasts() if (fc["co"], fc["metric"], fc["period"]) not in have]
    added = _positions_for(new, load_markets())
    ledger["positions"].extend(added)
    _rebalance(ledger)
    LEDGER.write_text(json.dumps(ledger, indent=2) + "\n")
    print(f"added {len(added)} weather bets; ledger now {len(ledger['positions'])}")
    for p in added:
        print(f"  {p['side']:3} {p['co']} ({p['period']}) >= {p['threshold']:g} @ {p['entry_price']:.2f}")
    return 0


def cmd_mark(args):
    from backtest.kalshi import list_markets
    ledger = json.loads(LEDGER.read_text())
    by_series = {}
    for p in ledger["positions"]:
        if p["status"] == "open":
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
            p["status"] = "resolved"; p["result"] = result
            p["realized_pnl"] = round(p["contracts"] * (1.0 if won else 0.0) - p["stake"], 2)
            p["resolved_date"] = now_iso()[:10]
            resolved += 1
    ledger["marked_at"] = now_iso()
    LEDGER.write_text(json.dumps(ledger, indent=2) + "\n")
    print(f"marked weather ledger: {resolved} newly resolved, "
          f"{sum(1 for p in ledger['positions'] if p['status']=='open')} still open")
    return 0


def cmd_rebalance(args):
    ledger = json.loads(LEDGER.read_text())
    _rebalance(ledger)
    LEDGER.write_text(json.dumps(ledger, indent=2) + "\n")
    n = sum(1 for p in ledger["positions"] if p["status"] == "open")
    print(f"resized all weather bets to a flat ${STAKE:.0f} ({n} open)")
    return 0


def main():
    global OPEN, FCST, LEDGER
    ap = argparse.ArgumentParser(prog="weather_portfolio")
    ap.add_argument("--track", default="wealthsimple", choices=["wealthsimple", "nearterm"])
    sub = ap.add_subparsers(dest="cmd", required=True)
    ip = sub.add_parser("init"); ip.add_argument("--force", action="store_true")
    sub.add_parser("add"); sub.add_parser("mark"); sub.add_parser("rebalance")
    args = ap.parse_args()
    c = cfg(args.track)
    OPEN, FCST, LEDGER = c["open"], c["fcst"], c["ledger"]
    return {"init": cmd_init, "add": cmd_add, "mark": cmd_mark, "rebalance": cmd_rebalance}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())

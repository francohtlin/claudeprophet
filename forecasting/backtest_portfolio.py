"""Assemble the backtest paper portfolio: blind forecasts on resolved metrics,
entered at pre-release market prices, settled at known outcomes.

Same position rule as the live portfolio: per metric, the contract where the
blind forecast most disagrees with the pre-release market price (min 5 pt gap,
price band 0.03-0.97), $100 paper stake. Also scores per-contract Brier for
forecast quality independent of trading.

Output: data/backtest_portfolio.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]
BLIND = ROOT / "data" / "forecasts" / "backtest_blind.jsonl"
PRICES = ROOT / "data" / "forecasts" / "backtest_prices.json"
OUT = ROOT / "data" / "backtest_portfolio.json"

BANKROLL = 1000.0
MIN_EDGE = 0.05
BAND = (0.03, 0.97)
WINDOW_START = "2026-07-14"   # backtest tracks the last week of resolutions
WINDOW_END = "2026-07-22"


def main() -> int:
    prices = json.loads(PRICES.read_text())
    forecasts = [json.loads(l) for l in BLIND.open() if "cp_median" in json.loads(l)]

    picks = []
    briers_cp, briers_mkt = [], []       # full resolved window (context)
    wk_cp, wk_mkt = [], []               # last week only
    for fc in forecasts:
        in_window = WINDOW_START <= fc["resolves"] <= WINDOW_END
        best = None
        for c in fc["contracts"]:
            pr = prices.get(c["ticker"])
            if not pr:
                continue
            mkt = pr["pre_yes"]
            if not (BAND[0] <= mkt <= BAND[1]):
                continue
            y = 1 if c["result"] == "yes" else 0
            briers_cp.append((c["cp_p"] - y) ** 2)
            briers_mkt.append((mkt - y) ** 2)
            if in_window:
                wk_cp.append((c["cp_p"] - y) ** 2)
                wk_mkt.append((mkt - y) ** 2)
            diff = c["cp_p"] - mkt
            if best is None or abs(diff) > abs(best["diff"]):
                best = {"diff": diff, "c": c, "mkt": mkt}
        if not in_window or best is None or abs(best["diff"]) < MIN_EDGE:
            continue
        picks.append({"fc": fc, **best})

    # $1k bankroll split equally across the week's qualifying positions
    n = len(picks)
    stake = round(BANKROLL / n, 2) if n else 0.0
    positions = []
    for pk in picks:
        fc, c, mkt = pk["fc"], pk["c"], pk["mkt"]
        side = "YES" if pk["diff"] > 0 else "NO"
        entry = mkt if side == "YES" else round(1 - mkt, 3)
        contracts_n = stake / entry
        won = (c["result"] == "yes") == (side == "YES")
        pnl = round(contracts_n * (1.0 if won else 0.0) - stake, 2)
        positions.append({
            "co": fc["co"], "metric": fc["metric"], "period": fc["period"],
            "resolves": fc["resolves"], "ticker": c["ticker"], "question": c["question"],
            "side": side, "entry": entry, "cp_p": c["cp_p"], "mkt_pre": mkt,
            "result": c["result"], "won": won, "stake": stake, "pnl": pnl,
        })

    total = sum(p["pnl"] for p in positions)
    wins = sum(1 for p in positions if p["won"])
    summary = {
        "bankroll_start": BANKROLL,
        "bankroll_end": round(BANKROLL + total, 2),
        "positions": n, "stake_per_position": stake,
        "realized_pnl": round(total, 2),
        "return_pct": round(total / BANKROLL * 100, 1),
        "wins": wins, "losses": n - wins,
        "win_rate": round(wins / n * 100) if n else None,
        "window": f"{WINDOW_START}..{WINDOW_END}", "mode": "blind_no_search",
        "week_brier_cp": round(sum(wk_cp) / len(wk_cp), 4) if wk_cp else None,
        "week_brier_market": round(sum(wk_mkt) / len(wk_mkt), 4) if wk_mkt else None,
        "week_scored_contracts": len(wk_cp),
        "alltime_brier_cp": round(sum(briers_cp) / len(briers_cp), 4) if briers_cp else None,
        "alltime_brier_market": round(sum(briers_mkt) / len(briers_mkt), 4) if briers_mkt else None,
        "alltime_scored_contracts": len(briers_cp),
        "min_edge": MIN_EDGE,
    }
    positions.sort(key=lambda p: p["pnl"])
    OUT.write_text(json.dumps({"summary": summary, "positions": positions}, indent=1) + "\n")
    print(json.dumps(summary, indent=2))
    print(f"\nworst 5:")
    for p in positions[:5]:
        print(f"  {p['pnl']:>8.0f}  {p['side']:3} {p['co']} — {p['metric']} @ {p['entry']:.2f} (cp {p['cp_p']:.2f} vs mkt {p['mkt_pre']:.2f}) -> {p['result']}")
    print(f"best 5:")
    for p in positions[-5:]:
        print(f"  {p['pnl']:>8.0f}  {p['side']:3} {p['co']} — {p['metric']} @ {p['entry']:.2f} (cp {p['cp_p']:.2f} vs mkt {p['mkt_pre']:.2f}) -> {p['result']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

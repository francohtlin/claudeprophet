"""Select the next N uncertain, most-liquid KPI metrics to forecast.

Groups open markets into metrics, keeps the genuinely uncertain ones (market
median inside the threshold ladder), skips anything already forecasted, ranks by
volume, and writes the top N to data/forecasts/_chosen.json for forecast_kpi.py.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from forecasting.kpi_metrics import FORECASTS, OPEN_MARKETS, group_markets, implied_median


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", "--num", type=int, default=30, help="How many metrics to select.")
    args = ap.parse_args()

    rows = [json.loads(l) for l in OPEN_MARKETS.open()]
    done = set()
    if FORECASTS.exists():
        for l in FORECASTS.open():
            r = json.loads(l)
            if "cp_median" in r:
                done.add((r["co"], r["metric"], r["period"], r["resolves"]))

    cands = []
    for (co, metric, period, rd), g in group_markets(rows).items():
        if (co, metric, period, rd) in done:
            continue
        ladder = [(v, p) for v, p, _ in g["markets"] if v is not None]
        imp = implied_median(ladder)
        if not imp or imp[0] != "~":  # keep only genuinely uncertain metrics
            continue
        cands.append({
            "co": co, "metric": metric, "period": period, "resolves": rd,
            "n": len(g["markets"]), "vol": sum(m[2] for m in g["markets"]),
            "market_median": imp[1], "thresholds": sorted(v for v, _, _ in g["markets"] if v is not None),
        })
    cands.sort(key=lambda c: -c["vol"])
    chosen = cands[: args.num]

    out = FORECASTS.parent / "_chosen.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    json.dump(chosen, out.open("w"))
    print(f"{len(cands)} uncertain metrics available; selected {len(chosen)} by volume -> {out}")
    for c in chosen[:10]:
        print(f"  vol {c['vol']:>8,}  {c['resolves']}  {c['co']} — {c['metric']} ({c['period']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

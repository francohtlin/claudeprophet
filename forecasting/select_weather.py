"""Select uncertain Kalshi weather ladders to forecast -> _chosen_weather.json.

Parallel to select_kpi/select_pm. Keeps genuinely uncertain multi-rung ladders
(market-implied median inside the ladder) not already forecast; ranks by soonest
close (weather volume is ~0, and near-term resolution builds the record fastest).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from forecasting.kpi_metrics import implied_median
from forecasting.weather_tracks import cfg


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", "--num", type=int, default=30)
    ap.add_argument("--track", default="wealthsimple", choices=["wealthsimple", "nearterm"])
    args = ap.parse_args()
    c = cfg(args.track)
    OPEN, FCST, OUT = c["open"], c["fcst"], c["chosen"]

    rows = [json.loads(l) for l in OPEN.open()] if OPEN.exists() else []
    done = set()
    if FCST.exists():
        for l in FCST.open():
            r = json.loads(l)
            if "cp_median" in r:
                done.add((r["co"], r["metric"], r["period"]))

    groups = defaultdict(list)
    for r in rows:
        groups[(r["company"], r["metric"], r["period"])].append(r)

    cands = []
    for (co, metric, period), rs in groups.items():
        if (co, metric, period) in done or len(rs) < 2:
            continue
        ladder = [(r["threshold"], r["yes_mid"]) for r in rs
                  if r.get("threshold") is not None and r.get("yes_mid") is not None]
        imp = implied_median(ladder)
        if not imp or imp[0] != "~":
            continue
        # Skip illiquid/placeholder ladders (prices clustered near 0.5, no real
        # price discovery). A genuinely-priced >= ladder spans a wide range.
        prices = [p for _, p in ladder]
        if max(prices) - min(prices) < 0.4:
            continue
        cands.append({"co": co, "metric": metric, "period": period,
                      "market_url": rs[0].get("market_url", ""),
                      "close": rs[0].get("close_time", ""),
                      "question": rs[0].get("question", ""),
                      "market_median": imp[1],
                      "thresholds": sorted(t for t, _ in ladder)})

    cands.sort(key=lambda c: c["close"])  # soonest-resolving first
    chosen = cands[: args.num]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    json.dump(chosen, OUT.open("w"))
    print(f"{len(cands)} uncertain weather ladders available; selected {len(chosen)} -> {OUT}")
    for c in chosen:
        print(f"  {c['close'][:10]}  {c['co']} ({c['period']})  mkt~{c['market_median']:g}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

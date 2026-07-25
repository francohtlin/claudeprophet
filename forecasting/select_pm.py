"""Select Polymarket KPI metrics to forecast -> data/forecasts/_chosen_pm.json.

Parallel to select_kpi.py. Two kinds:
  * ladder  (threshold / bucket_cdf): keep the genuinely uncertain ones (market
    implied median lands inside the ladder), rank by volume.
  * binary  (earnings beats/misses): keep the ones the market isn't already sure
    about (price in a middle band), so a forecast can actually disagree.
Skips metrics already forecasted in open_pm_claudeprophet.jsonl.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from forecasting.kpi_metrics import implied_median

ROOT = Path(__file__).resolve().parents[1]
OPEN = ROOT / "data" / "polymarket_kpi_open.jsonl"
FCST = ROOT / "data" / "forecasts" / "open_pm_claudeprophet.jsonl"
OUT = ROOT / "data" / "forecasts" / "_chosen_pm.json"

BINARY_BAND = (0.05, 0.95)   # only forecast beats the market isn't already sure of


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", "--num", type=int, default=30)
    args = ap.parse_args()

    rows = [json.loads(l) for l in OPEN.open()] if OPEN.exists() else []
    done = set()
    if FCST.exists():
        for l in FCST.open():
            r = json.loads(l)
            if "cp_p" in r or "cp_median" in r:
                done.add((r["co"], r["metric"], r["period"]))

    groups = defaultdict(list)
    for r in rows:
        groups[(r["company"], r["metric"], r["period"])].append(r)

    cands = []
    for (co, metric, period), rs in groups.items():
        if (co, metric, period) in done:
            continue
        kind = rs[0]["outcome_kind"]
        vol = max((r.get("volume") or 0) for r in rs)
        url = rs[0].get("market_url", "")
        close = rs[0].get("close_time", "")
        if kind == "binary":
            mid = rs[0].get("yes_mid")
            if mid is None or not (BINARY_BAND[0] <= mid <= BINARY_BAND[1]):
                continue
            cands.append({"co": co, "metric": metric, "period": period, "kind": "binary",
                          "market_url": url, "close": close, "vol": vol,
                          "market_p": mid, "question": rs[0].get("question", "")})
        else:
            ladder = [(r["threshold"], r["yes_mid"]) for r in rs
                      if r.get("threshold") is not None and r.get("yes_mid") is not None]
            imp = implied_median(ladder)
            if not imp or imp[0] != "~":
                continue
            cands.append({"co": co, "metric": metric, "period": period, "kind": "ladder",
                          "market_url": url, "close": close, "vol": vol,
                          "market_median": imp[1],
                          "thresholds": sorted(t for t, _ in ladder)})

    cands.sort(key=lambda c: -c["vol"])
    chosen = cands[: args.num]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    json.dump(chosen, OUT.open("w"))
    nlad = sum(1 for c in chosen if c["kind"] == "ladder")
    nbin = sum(1 for c in chosen if c["kind"] == "binary")
    print(f"{len(cands)} PM metrics available; selected {len(chosen)} "
          f"({nlad} ladder, {nbin} binary) -> {OUT}")
    for c in chosen:
        print(f"  [{c['kind']:6}] vol {c['vol']:>10,.0f}  {c['co']} — {c['metric'][:38]} ({c['period'] or '?'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

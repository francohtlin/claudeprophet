"""SPIKE: prove the Polymarket source end-to-end, read-only.

Discovers Polymarket company-KPI events live, normalizes them into the unified
ladder schema (shapes A/B/C, buckets -> CDF), writes them to a NEW additive file
data/polymarket_kpi_open.jsonl, and cross-joins against the existing Kalshi data
to show same-KPI venue spreads. Changes NOTHING in the live pipeline.

Run:  python3 forecasting/spike_polymarket.py
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from forecasting.kpi_metrics import implied_median, fmtv
from forecasting.sources import KalshiSource, PolymarketSource, normalize_company

OUT = Path(__file__).resolve().parents[1] / "data" / "polymarket_kpi_open.jsonl"


def ladders(rows):
    """Group unified rows into metric ladders keyed by (company, metric, period)."""
    g = defaultdict(list)
    for r in rows:
        g[(r["company"], r["metric"], r["period"])].append(r)
    return g


def median_of(rows):
    lad = [(r["threshold"], r["yes_mid"]) for r in rows
           if r.get("threshold") is not None and r.get("yes_mid") is not None]
    return implied_median(lad) if lad else None


def main() -> int:
    print("== discovering Polymarket company-KPI events (live Gamma) ==")
    pm = PolymarketSource()
    pm_rows = pm.discover()
    print(f"   events={pm.debug['events']}  by_shape={pm.debug['by_shape']}  "
          f"skipped_malformed={pm.debug['skipped_malformed']}  ->  {len(pm_rows)} rows")

    OUT.write_text("".join(json.dumps(r) + "\n" for r in pm_rows))
    print(f"   wrote {len(pm_rows)} unified rows -> {OUT.name} (new, additive)\n")

    pm_lad = ladders(pm_rows)
    print(f"== {len(pm_lad)} Polymarket metrics; sample ladders by shape ==")
    shown = {"binary": 0, "threshold": 0, "bucket_cdf": 0}
    for (co, metric, period), rs in sorted(pm_lad.items(), key=lambda kv: -sum(x["volume"] for x in kv[1])):
        kind = rs[0]["outcome_kind"]
        if shown.get(kind, 9) >= 2:
            continue
        shown[kind] = shown.get(kind, 0) + 1
        med = median_of(rs)
        tag = f"median~{fmtv(med[1])}" if med and med[0] == "~" else (f"P(Yes)={rs[0]['yes_mid']}" if kind == "binary" else "off-ladder")
        print(f"  [{kind:10}] {co} — {metric[:40]} ({period or '?'})  {tag}")
        for r in sorted(rs, key=lambda x: (x['threshold'] is None, x['threshold'] or 0))[:6]:
            t = f">= {fmtv(r['threshold'])}" if r["threshold"] is not None else "binary"
            print(f"        {t:>12}  P(Yes)={r['yes_mid']}")
        print(f"        link: {rs[0]['market_url']}")

    # ---- cross-source venue spread ----
    print("\n== cross-source join (allow both; show venue spread) ==")
    kal_rows = KalshiSource().discover()
    def index(rows):
        idx = defaultdict(list)
        for (co, metric, period), rs in ladders(rows).items():
            idx[(normalize_company(co), period)].append((metric, median_of(rs)))
        return idx
    ki, pi = index(kal_rows), index(pm_rows)
    both = sorted(set(ki) & set(pi))
    if not both:
        print("   no (company, period) present on both venues right now")
    for co, period in both:
        kmed = next((m for _, m in ki[(co, period)] if m and m[0] == "~"), None)
        pmed = next((m for _, m in pi[(co, period)] if m and m[0] == "~"), None)
        kl = ", ".join(sorted({m for m, _ in ki[(co, period)]}))[:40]
        pl = ", ".join(sorted({m for m, _ in pi[(co, period)]}))[:40]
        spread = f"spread={fmtv(abs(kmed[1]-pmed[1]))}" if (kmed and pmed) else ""
        print(f"  {co} ({period or '?'})  {spread}")
        print(f"      kalshi:     {kl}  {'~'+fmtv(kmed[1]) if kmed else ''}")
        print(f"      polymarket: {pl}  {'~'+fmtv(pmed[1]) if pmed else ''}")
    print(f"\n   kalshi metrics={len(ki)}  polymarket metrics={len(pi)}  overlap (company,period)={len(both)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

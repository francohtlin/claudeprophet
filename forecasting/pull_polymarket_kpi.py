"""Pull open Polymarket company-KPI markets -> data/polymarket_kpi_open.jsonl.

Parallel to pull_kpi_markets.py (Kalshi). Uses the PolymarketSource to discover
company-report/KPI events from the Gamma API and normalize the three market
shapes (cumulative ladder, range buckets -> CDF, single binary) into the unified
row schema. Kept in a SEPARATE file so the Kalshi and Polymarket tracks run in
parallel and never interfere.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from forecasting.sources import PolymarketSource

OUT = Path(__file__).resolve().parents[1] / "data" / "polymarket_kpi_open.jsonl"


def main() -> int:
    src = PolymarketSource()
    rows = src.discover()
    rows.sort(key=lambda r: (r.get("close_time") or "", r.get("company") or ""))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("".join(json.dumps(r) + "\n" for r in rows))
    kinds = src.debug.get("by_shape", {})
    print(f"wrote {len(rows)} open Polymarket KPI rows "
          f"(events={src.debug.get('events')}, shapes={kinds}, "
          f"skipped_malformed={src.debug.get('skipped_malformed')}) -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

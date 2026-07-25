"""Pull open Polymarket markets for a track -> data/polymarket_*_open.jsonl.

Parallel to pull_kpi_markets.py (Kalshi). Uses the PolymarketSource to discover
company events from the Gamma API and normalize the three market shapes
(cumulative ladder, range buckets -> CDF, single binary) into the unified row
schema. `--track earnings` (default) pulls the beat/miss markets; `--track kpis`
pulls the revenue/deliveries/growth ladders & buckets. Separate output files so
the tracks never interfere.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from forecasting.sources import PolymarketSource
from forecasting.pm_tracks import cfg


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--track", default="earnings", choices=["earnings", "kpis"])
    args = ap.parse_args()
    c = cfg(args.track)

    src = PolymarketSource(**c["source"])
    rows = src.discover()
    rows.sort(key=lambda r: (r.get("close_time") or "", r.get("company") or ""))
    out = c["open"]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(r) + "\n" for r in rows))
    print(f"wrote {len(rows)} open Polymarket {c['label']} rows "
          f"(events={src.debug.get('events')}, shapes={src.debug.get('by_shape', {})}, "
          f"skipped_malformed={src.debug.get('skipped_malformed')}) -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

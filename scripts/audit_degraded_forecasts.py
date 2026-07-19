#!/usr/bin/env python3
"""Report /predict responses that used degraded forecast sources."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
AUDIT_JSONL = ROOT / "logs" / "prediction_audit.jsonl"
GOOD_SOURCES = {"claude_stdout", "final_checkpoint"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    rows = degraded_rows(limit=args.limit)
    if args.json:
        print(json.dumps(rows, indent=2, sort_keys=True))
    else:
        if not rows:
            print("No degraded real forecast responses found.")
            return 0
        for row in rows:
            print(
                f"{row['completed_at']} {row['forecast_source']} "
                f"{row['event_ticker'] or row['market_ticker'] or '-'} "
                f"{row['title'] or '-'} [{row['request_id']}]"
            )
    return 0


def degraded_rows(*, limit: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not AUDIT_JSONL.exists():
        return rows
    for line in AUDIT_JSONL.read_text(errors="replace").splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("stage") != "predict_complete":
            continue
        source = entry.get("forecast_source")
        if source in GOOD_SOURCES:
            continue
        event = entry.get("event") if isinstance(entry.get("event"), dict) else {}
        title = str(event.get("title") or "")
        ticker = str(event.get("event_ticker") or event.get("market_ticker") or "")
        if is_synthetic(title=title, ticker=ticker):
            continue
        rows.append(
            {
                "completed_at": entry.get("completed_at"),
                "request_id": entry.get("request_id"),
                "forecast_source": source,
                "event_ticker": event.get("event_ticker"),
                "market_ticker": event.get("market_ticker"),
                "title": event.get("title"),
                "duration_seconds": entry.get("duration_seconds"),
            }
        )
    return rows[-limit:]


def is_synthetic(*, title: str, ticker: str) -> bool:
    text = f"{title} {ticker}".lower()
    synthetic_markers = (
        "smoke",
        "stress",
        "test event",
        "live probe",
        "endpoint",
        "observer hook",
        "viewer smoke",
    )
    return any(marker in text for marker in synthetic_markers)


if __name__ == "__main__":
    raise SystemExit(main())

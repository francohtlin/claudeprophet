from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def build_actuals(events: list[dict[str, Any]]) -> dict[str, Any]:
    actuals: dict[str, Any] = {}
    for event in events:
        ticker = event.get("market_ticker")
        resolved = event.get("resolved_outcome")
        if not ticker or resolved is None:
            continue
        actuals[str(ticker)] = resolved
    return actuals


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(
            "Usage: python -m sample_tools.build_actuals <events.json> <actuals.json>"
        )

    input_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2])
    events = json.loads(input_path.read_text())
    if not isinstance(events, list):
        raise SystemExit(f"{input_path} must contain a JSON list of events.")

    actuals = build_actuals(events)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(actuals, indent=2, sort_keys=True) + "\n")
    print(f"Wrote {len(actuals)} actuals -> {output_path}")


if __name__ == "__main__":
    main()

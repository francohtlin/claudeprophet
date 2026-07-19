#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_ENDPOINT = "https://predict.hansonwen.dev/predict"
DEFAULT_HEALTH = "https://predict.hansonwen.dev/health"
DEFAULT_OUTPUT = Path("logs") / "live_soak_predictions.jsonl"


EVENT_TEMPLATES: list[dict[str, Any]] = [
    {
        "event_ticker": "SOAK-FOMC-JUN2026-CUT",
        "market_ticker": "SOAK-FOMC-JUN2026-CUT-YES",
        "title": "Will the Federal Reserve lower the target federal funds rate at or before its June 2026 FOMC meeting?",
        "description": "Forecast whether the Federal Open Market Committee will announce a lower target range for the federal funds rate at or before the scheduled June 2026 FOMC meeting, compared with the target range in effect on May 18, 2026.",
        "category": "Economics",
        "close_time": "2026-06-17T18:00:00Z",
        "resolution_rules": "Resolve Yes if the FOMC announces a target federal funds rate range after any meeting or intermeeting action on or before June 17, 2026 that is lower than the target range in effect on May 18, 2026. Resolve No otherwise.",
        "outcomes": ["Yes", "No"],
    },
    {
        "event_ticker": "SOAK-BTC-JUN2026-125K",
        "market_ticker": "SOAK-BTC-JUN2026-125K-YES",
        "title": "Will Bitcoin trade above $125,000 at any point before July 1, 2026?",
        "description": "Forecast whether BTC/USD will trade above 125000.00 on a major spot exchange at any time before July 1, 2026.",
        "category": "Crypto",
        "close_time": "2026-07-01T00:00:00Z",
        "resolution_rules": "Resolve Yes if a major spot BTC/USD market reports a trade strictly above 125000.00 before July 1, 2026 UTC. Resolve No otherwise.",
        "outcomes": ["Yes", "No"],
    },
    {
        "event_ticker": "SOAK-NBA-2026-FINALS-CELTICS",
        "market_ticker": "SOAK-NBA-2026-FINALS-CELTICS-YES",
        "title": "Will the Boston Celtics win the 2026 NBA Finals?",
        "description": "Forecast whether the Boston Celtics will be the official winner of the 2026 NBA Finals.",
        "category": "Sports",
        "close_time": "2026-06-30T04:00:00Z",
        "resolution_rules": "Resolve Yes if the Boston Celtics win the 2026 NBA Finals. Resolve No if any other team wins.",
        "outcomes": ["Yes", "No"],
    },
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run public CodexProphet live endpoint smoke/soak checks.")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--health-url", default=DEFAULT_HEALTH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--iterations", type=int, default=1)
    parser.add_argument("--interval-seconds", type=float, default=0)
    parser.add_argument("--timeout-seconds", type=float, default=150)
    parser.add_argument("--event-index", type=int, default=None, help="Use one event template index for every run.")
    args = parser.parse_args()

    if args.iterations < 1:
        raise SystemExit("--iterations must be positive")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    failures = 0
    for index in range(args.iterations):
        template = EVENT_TEMPLATES[args.event_index % len(EVENT_TEMPLATES)] if args.event_index is not None else EVENT_TEMPLATES[index % len(EVENT_TEMPLATES)]
        event = dict(template)
        event["event_ticker"] = f"{template['event_ticker']}-{uuid.uuid4().hex[:8]}"
        event["market_ticker"] = f"{template['market_ticker']}-{uuid.uuid4().hex[:8]}"
        record = run_once(args.health_url, args.endpoint, event, timeout=args.timeout_seconds)
        append_jsonl(args.output, record)
        print(json.dumps(record, sort_keys=True), flush=True)
        if not record.get("ok"):
            failures += 1
        if index != args.iterations - 1 and args.interval_seconds > 0:
            time.sleep(args.interval_seconds)

    return 1 if failures else 0


def run_once(health_url: str, endpoint: str, event: dict[str, Any], *, timeout: float) -> dict[str, Any]:
    started = time.monotonic()
    record: dict[str, Any] = {
        "started_at": iso_now(),
        "endpoint": endpoint,
        "event_ticker": event.get("event_ticker"),
        "category": event.get("category"),
        "ok": False,
    }
    try:
        health = request_json("GET", health_url, None, timeout=20)
        record["health"] = health
        response = request_json("POST", endpoint, event, timeout=timeout)
        record["response"] = response
        record["validation"] = validate_prediction(response, event)
        record["ok"] = record["validation"]["valid"]
    except Exception as exc:
        record["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        record["duration_seconds"] = round(time.monotonic() - started, 3)
        record["completed_at"] = iso_now()
    return record


def request_json(method: str, url: str, payload: dict[str, Any] | None, *, timeout: float) -> Any:
    data = None
    headers = {
        "accept": "application/json",
        "user-agent": "Mozilla/5.0 CodexProphetLiveSoak/1.0",
    }
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["content-type"] = "application/json"
    request = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            return json.loads(body)
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {body[:500]}") from exc
    except URLError as exc:
        raise RuntimeError(str(exc)) from exc


def validate_prediction(response: Any, event: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    if not isinstance(response, dict):
        return {"valid": False, "errors": ["response is not an object"]}
    probabilities = response.get("probabilities")
    if not isinstance(probabilities, list) or not probabilities:
        errors.append("missing probabilities list")
    else:
        seen = set()
        expected = set(event.get("outcomes") or [])
        for item in probabilities:
            if not isinstance(item, dict):
                errors.append("probability item is not an object")
                continue
            market = item.get("market")
            probability = item.get("probability")
            seen.add(market)
            if not isinstance(probability, (int, float)) or not 0 <= float(probability) <= 1:
                errors.append(f"invalid probability for {market!r}")
        if expected and seen != expected:
            errors.append(f"outcomes mismatch: expected {sorted(expected)}, saw {sorted(seen)}")
    rationale = response.get("rationale")
    if not isinstance(rationale, str) or len(rationale.strip()) < 20:
        errors.append("missing or too-short rationale")
    return {"valid": not errors, "errors": errors}


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    sys.exit(main())

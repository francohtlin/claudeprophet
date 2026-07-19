#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any


TERMINAL_STAGES = {
    "prediction_validated",
    "validation_failed",
    "fallback_used",
    "claude_timeout",
    "claude_failed",
    "claude_timeout_checkpoint_returned",
    "degraded_forecast_returned",
    "claude_final_checkpoint_detected",
}

FAILURE_OR_DEGRADED_STAGES = {
    "validation_failed",
    "fallback_used",
    "claude_timeout",
    "claude_failed",
    "claude_timeout_checkpoint_returned",
    "degraded_forecast_returned",
}


def main() -> int:
    args = parse_args()
    workspace = Path(args.workspace)
    trace = wait_for_terminal(workspace, args.max_wait_seconds)
    report = compose_report(
        workspace=workspace,
        request_id=args.request_id,
        event_title=args.event_title,
        event_ticker=args.event_ticker,
        trace=trace,
    )
    stage = last_terminal_stage(trace)
    if stage == "prediction_validated" and args.success_email_to:
        send_success_email(args, workspace, trace)
    if stage in FAILURE_OR_DEGRADED_STAGES and args.failure_email_to:
        send_failure_email(args, workspace, stage)
    if args.delivery_to:
        send_message(args.openclaw_bin, args.delivery_channel, args.delivery_to, report)
    else:
        print(report)
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Watch one ClaudeProphet request workspace and report the result.")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--event-title", required=True)
    parser.add_argument("--event-ticker")
    parser.add_argument("--max-wait-seconds", type=float, default=900)
    parser.add_argument("--openclaw-bin", default="openclaw")
    parser.add_argument("--delivery-channel", default="telegram")
    parser.add_argument("--delivery-to")
    parser.add_argument("--success-email-to")
    parser.add_argument("--success-email-account", default="wenhanson0@gmail.com")
    parser.add_argument("--failure-email-to")
    parser.add_argument("--failure-email-account", default="wenhanson0@gmail.com")
    return parser.parse_args()


def wait_for_terminal(workspace: Path, max_wait_seconds: float) -> list[dict[str, Any]]:
    deadline = time.monotonic() + max_wait_seconds
    saw_prediction_file_at: float | None = None
    while True:
        trace = read_trace(workspace / "trace.jsonl")
        if last_terminal_stage(trace):
            return trace
        if (workspace / "claude_final.json").exists() or (workspace / "final_submission.json").exists():
            saw_prediction_file_at = saw_prediction_file_at or time.monotonic()
            if time.monotonic() - saw_prediction_file_at >= 5:
                return trace
        if time.monotonic() >= deadline:
            return trace
        time.sleep(2)


def read_trace(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def last_terminal_stage(trace: list[dict[str, Any]]) -> str | None:
    for row in reversed(trace):
        stage = row.get("stage")
        if stage in TERMINAL_STAGES:
            return stage
    return None


def compose_report(
    *,
    workspace: Path,
    request_id: str,
    event_title: str,
    event_ticker: str | None,
    trace: list[dict[str, Any]],
) -> str:
    stage = last_terminal_stage(trace)
    if stage == "prediction_validated":
        status = "succeeded"
    elif stage in FAILURE_OR_DEGRADED_STAGES:
        status = "failed"
    elif (workspace / "claude_final.json").exists() or (workspace / "final_submission.json").exists():
        status = "prediction file visible; validation stage not visible yet"
    else:
        status = "timed out waiting for terminal stage"

    validation_row = next((row for row in reversed(trace) if row.get("stage") == "prediction_validated"), {})
    duration = validation_row.get("duration_seconds")
    forecast_source = validation_row.get("forecast_source") or infer_forecast_source(workspace)
    stages = " -> ".join(row.get("stage", "?") for row in trace if row.get("stage"))
    evidence = summarize_evidence(workspace / "evidence_manifest.json")
    final_json = load_final_json(workspace)
    market_adjustment = summarize_market_adjustment(workspace)
    errors = summarize_errors(workspace, stage)

    ticker = f" ({event_ticker})" if event_ticker else ""
    lines = [
        "ClaudeProphet observer report",
        f"Status: {status}",
        f"Task/event: {event_title}{ticker}",
        f"Request ID: {request_id}",
        f"Workspace: {workspace}",
        f"Duration: {duration:.1f}s" if isinstance(duration, (int, float)) else "Duration: not visible",
        f"Forecast source: {forecast_source or 'not visible'}",
        "Market adjustment vs Kalshi:",
        *market_adjustment,
        f"Trace stages: {stages or 'not visible'}",
        f"Evidence/tools used by Claude: {evidence or 'not visible'}",
        f"Errors/fallback behavior: {errors}",
        "",
        "Final output JSON:",
        final_json or "not visible",
    ]
    return "\n".join(lines)


def infer_forecast_source(workspace: Path) -> str | None:
    if (workspace / "final_submission.json").exists():
        return "final_checkpoint"
    if (workspace / "claude_final.json").exists():
        return "claude_final"
    return None


def summarize_evidence(path: Path) -> str:
    data = load_json(path)
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return ""
    summaries = []
    for item in items[:10]:
        if not isinstance(item, dict):
            continue
        source = item.get("source") or "unknown"
        kind = item.get("kind") or "evidence"
        notes = str(item.get("notes") or "").replace("\n", " ")
        if len(notes) > 160:
            notes = notes[:157] + "..."
        summaries.append(f"{kind}:{source}" + (f" ({notes})" if notes else ""))
    remaining = len(items) - len(summaries)
    if remaining > 0:
        summaries.append(f"+{remaining} more")
    return "; ".join(summaries)


def load_final_json(workspace: Path) -> str:
    for path in [workspace / "claude_final.json", workspace / "final_submission.json"]:
        data = load_json(path)
        if data is None:
            continue
        if path.name == "final_submission.json" and isinstance(data, dict) and isinstance(data.get("prediction"), dict):
            data = data["prediction"]
        text = json.dumps(data, sort_keys=True, separators=(",", ":"))
        if len(text) > 3500:
            text = text[:3497] + "..."
        return text
    return ""


def load_final_prediction(workspace: Path) -> dict[str, Any]:
    for path in [workspace / "claude_final.json", workspace / "final_submission.json"]:
        data = load_json(path)
        if not isinstance(data, dict):
            continue
        if path.name == "final_submission.json" and isinstance(data.get("prediction"), dict):
            return data["prediction"]
        return data
    return {}


def format_probability_rows(prediction: dict[str, Any]) -> list[str]:
    probabilities = prediction.get("probabilities")
    if not isinstance(probabilities, list):
        return ["- not visible"]
    rows: list[tuple[str, float | None]] = []
    for item in probabilities:
        if not isinstance(item, dict):
            continue
        market = str(item.get("market") or item.get("outcome") or "unknown")
        probability = item.get("probability")
        rows.append((market, probability if isinstance(probability, (int, float)) else None))
    rows.sort(key=lambda row: -1 if row[1] is None else -row[1])
    formatted = []
    for market, probability in rows:
        if probability is None:
            formatted.append(f"- {market}: not visible")
        else:
            formatted.append(f"- {market}: {probability * 100:.1f}%")
    return formatted or ["- not visible"]


def summarize_market_adjustment(workspace: Path) -> list[str]:
    prediction = load_final_prediction(workspace)
    probabilities = prediction.get("probabilities")
    if not isinstance(probabilities, list):
        return ["- not visible"]

    market_rates = kalshi_market_rates(workspace, probabilities)
    if not market_rates:
        return ["- Kalshi market rate not visible"]

    rows: list[str] = []
    for item in probabilities:
        if not isinstance(item, dict):
            continue
        market = str(item.get("market") or item.get("outcome") or "unknown")
        probability = item.get("probability")
        if not isinstance(probability, (int, float)):
            continue
        market_rate = market_rates.get(canonical_market_label(market))
        if market_rate is None:
            rows.append(f"- {market}: forecast {probability * 100:.1f}%, Kalshi not visible")
            continue
        delta = probability - market_rate
        rows.append(
            f"- {market}: forecast {probability * 100:.1f}%, "
            f"Kalshi {market_rate * 100:.1f}%, adjusted {delta * 100:+.1f} pp"
        )
    return rows or ["- not visible"]


def kalshi_market_rates(workspace: Path, probabilities: list[Any]) -> dict[str, float]:
    labels = [
        str(item.get("market") or item.get("outcome") or "")
        for item in probabilities
        if isinstance(item, dict)
    ]
    rates: dict[str, float] = {}
    event = load_json(workspace / "event.json")

    event_rates = event_market_rates(workspace / "event.json", labels)
    rates.update(event_rates)

    structured_rates = structured_kalshi_market_rates(workspace, event, labels)
    rates.update(structured_rates)

    evidence_rates = evidence_market_rates(workspace / "evidence_manifest.json", labels)
    rates.update(evidence_rates)
    return rates


def structured_kalshi_market_rates(workspace: Path, event: Any, labels: list[str]) -> dict[str, float]:
    records = structured_kalshi_records(workspace / "evidence_manifest.json")
    if not records:
        records = raw_kalshi_snapshot_records(workspace / "kalshi_event_snapshot.json")
    if not records:
        return {}
    return rates_from_kalshi_records(records, event if isinstance(event, dict) else {}, labels)


def structured_kalshi_records(path: Path) -> list[dict[str, Any]]:
    data = load_json(path)
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []

    records: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict) or item.get("usable") is False:
            continue
        source = str(item.get("source") or "")
        kind = str(item.get("kind") or "")
        if "kalshi" not in f"{source} {kind}".lower():
            continue
        summary = item.get("summary")
        markets = summary.get("markets") if isinstance(summary, dict) else None
        if isinstance(markets, list):
            records.extend(market for market in markets if isinstance(market, dict))
    return records


def raw_kalshi_snapshot_records(path: Path) -> list[dict[str, Any]]:
    data = load_json(path)
    markets = data.get("markets") if isinstance(data, dict) else None
    if not isinstance(markets, list):
        return []
    return [market for market in markets if isinstance(market, dict)]


def rates_from_kalshi_records(records: list[dict[str, Any]], event: dict[str, Any], labels: list[str]) -> dict[str, float]:
    if is_binary_yes_no_labels(labels):
        market = select_target_kalshi_record(records, event)
        if market is None:
            return {}
        probability = probability_from_kalshi_record(market)
        if probability is None:
            return {}
        return {
            canonical_market_label(label): probability if canonical_market_label(label) == "yes" else 1.0 - probability
            for label in labels
        }

    by_label: dict[str, dict[str, Any]] = {}
    for record in records:
        label = kalshi_record_label(record)
        if label:
            by_label[canonical_market_label(label)] = record

    rates: dict[str, float] = {}
    for label in labels:
        record = by_label.get(canonical_market_label(label))
        if record is None:
            continue
        probability = probability_from_kalshi_record(record)
        if probability is not None:
            rates[canonical_market_label(label)] = probability
    return rates


def select_target_kalshi_record(records: list[dict[str, Any]], event: dict[str, Any]) -> dict[str, Any] | None:
    market_ticker = event.get("market_ticker")
    if isinstance(market_ticker, str) and market_ticker.strip():
        normalized = market_ticker.strip().upper()
        for record in records:
            ticker = record.get("ticker")
            if isinstance(ticker, str) and ticker.strip().upper() == normalized:
                return record
    if len(records) == 1:
        return records[0]
    return None


def is_binary_yes_no_labels(labels: list[str]) -> bool:
    return {canonical_market_label(label) for label in labels} == {"yes", "no"}


def kalshi_record_label(record: dict[str, Any]) -> str | None:
    for key in ("label", "yes_sub_title", "subtitle", "no_sub_title"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def probability_from_kalshi_record(record: dict[str, Any]) -> float | None:
    midpoint = normalize_probability(record.get("midpoint"))
    if midpoint is not None:
        return midpoint

    book_probability = probability_from_kalshi_orderbook(record)
    if book_probability is not None:
        return book_probability

    # Last trade is useful when Kalshi exposes no current quote, but thin
    # contracts can have stale prints that diverge from the displayed book.
    for key in ("last_price", "last_price_dollars", "price", "probability"):
        probability = normalize_probability(record.get(key))
        if probability is not None:
            return probability

    return None


def probability_from_kalshi_orderbook(record: dict[str, Any]) -> float | None:
    yes_bid = normalize_probability(record.get("yes_bid", record.get("yes_bid_dollars")))
    yes_ask = normalize_probability(record.get("yes_ask", record.get("yes_ask_dollars")))
    if yes_bid is not None and yes_ask is not None:
        return (yes_bid + yes_ask) / 2.0

    no_bid = normalize_probability(record.get("no_bid", record.get("no_bid_dollars")))
    no_ask = normalize_probability(record.get("no_ask", record.get("no_ask_dollars")))
    if no_bid is not None and no_ask is not None:
        return 1.0 - ((no_bid + no_ask) / 2.0)
    return None


def event_market_rates(path: Path, labels: list[str]) -> dict[str, float]:
    event = load_json(path)
    if not isinstance(event, dict):
        return {}
    raw = event.get("market_data") or event.get("market_info")
    if not isinstance(raw, dict):
        return {}

    rates: dict[str, float] = {}
    for label in labels:
        record = raw.get(label)
        if isinstance(record, (int, float)):
            probability = normalize_probability(float(record))
        elif isinstance(record, dict):
            probability = probability_from_market_record(record)
        else:
            probability = None
        if probability is not None:
            rates[canonical_market_label(label)] = probability
    return rates


def evidence_market_rates(path: Path, labels: list[str]) -> dict[str, float]:
    data = load_json(path)
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return {}

    rates: dict[str, float] = {}
    for item in items:
        if not isinstance(item, dict) or item.get("usable") is False:
            continue
        source = str(item.get("source") or "")
        notes = str(item.get("notes") or "")
        haystack = f"{source} {notes}".lower()
        if "kalshi" not in haystack:
            continue
        for label in labels:
            probability = probability_from_evidence_notes(label, notes)
            if probability is not None:
                rates[canonical_market_label(label)] = probability
    return rates


def probability_from_evidence_notes(label: str, notes: str) -> float | None:
    variants = label_variants(label)
    for variant in variants:
        probability = probability_after_label(variant, notes)
        if probability is not None:
            return probability
        probability = probability_from_each_group(variant, notes)
        if probability is not None:
            return probability
    return None


def probability_after_label(label: str, notes: str) -> float | None:
    escaped = re.escape(label)
    patterns = [
        rf"(?<![\w.]){escaped}(?![\w.%])\s+bid/ask\s+({_NUMBER})/({_NUMBER})(?:\s+last\s+({_NUMBER}))?",
        rf"(?<![\w.]){escaped}(?![\w.%])\s+({_NUMBER})/({_NUMBER})\s+mid\s+({_NUMBER})",
        rf"(?<![\w.]){escaped}(?![\w.%])\s+({_NUMBER})/({_NUMBER})(?:\s+last\s+({_NUMBER}))?",
        rf"(?<![\w.]){escaped}(?![\w.%])[^.;]*?\b(?:mid|probability|price|near|around)\s+({_NUMBER})%?",
    ]
    for pattern in patterns:
        match = re.search(pattern, notes, flags=re.IGNORECASE)
        if not match:
            continue
        values = [value for value in match.groups() if value is not None]
        if "mid" in pattern and len(values) >= 3:
            return normalize_probability(values[2])
        if "bid/ask" in pattern or "/" in pattern:
            first = normalize_probability(values[0]) if values else None
            second = normalize_probability(values[1]) if len(values) > 1 else None
            if first is not None and second is not None:
                return (first + second) / 2.0
        if values:
            return normalize_probability(values[-1])
    return None


def probability_from_each_group(label: str, notes: str) -> float | None:
    for match in re.finditer(
        rf"(?P<labels>(?:[A-Za-z0-9_.%<>+-]+(?:\s*,\s*)?)+)\s+each\s+"
        rf"(?P<bid>{_NUMBER})/(?P<ask>{_NUMBER})(?:\s+last\s+(?P<last>{_NUMBER}))?",
        notes,
        flags=re.IGNORECASE,
    ):
        labels = [part.strip() for part in match.group("labels").split(",") if part.strip()]
        if canonical_market_label(label) not in {canonical_market_label(part) for part in labels}:
            continue
        bid = normalize_probability(match.group("bid"))
        ask = normalize_probability(match.group("ask"))
        if bid is not None and ask is not None:
            return (bid + ask) / 2.0
    return None


def probability_from_market_record(record: dict[str, Any]) -> float | None:
    yes_bid = normalize_probability(record.get("yes_bid"))
    yes_ask = normalize_probability(record.get("yes_ask"))
    if yes_bid is not None and yes_ask is not None:
        return (yes_bid + yes_ask) / 2.0
    for key in ("last_price", "previous_price", "price", "probability"):
        value = normalize_probability(record.get(key))
        if value is not None:
            return value
    no_bid = normalize_probability(record.get("no_bid"))
    no_ask = normalize_probability(record.get("no_ask"))
    if no_bid is not None and no_ask is not None:
        return 1.0 - ((no_bid + no_ask) / 2.0)
    return None


def normalize_probability(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not number == number:
        return None
    if number > 1.0:
        number /= 100.0
    return min(0.999, max(0.0, number))


_NUMBER = r"(?:\d+(?:\.\d+)?|\.\d+)"


def label_variants(label: str) -> list[str]:
    variants = [label]
    if label.endswith("%"):
        variants.append(label[:-1])
    if label.startswith("Above ") and label.endswith("%"):
        variants.append(label[:-1])
    above_match = re.match(r"^Above\s+(.+?)%?$", label, flags=re.IGNORECASE)
    if above_match:
        threshold = above_match.group(1).rstrip("%")
        variants.extend([f">{threshold}", f"> {threshold}", f">{threshold}%", f"> {threshold}%"])
    seen: set[str] = set()
    ordered: list[str] = []
    for variant in variants:
        key = variant.strip()
        if key and key not in seen:
            ordered.append(variant)
            seen.add(key)
    return ordered


def canonical_market_label(label: str) -> str:
    return re.sub(r"\s+", " ", label.strip().rstrip("%").lower())


def compose_success_email(args: argparse.Namespace, workspace: Path, trace: list[dict[str, Any]]) -> str:
    validation_row = next((row for row in reversed(trace) if row.get("stage") == "prediction_validated"), {})
    duration = validation_row.get("duration_seconds")
    forecast_source = validation_row.get("forecast_source") or infer_forecast_source(workspace)
    prediction = load_final_prediction(workspace)
    rationale = str(prediction.get("rationale") or "").strip()
    if len(rationale) > 2500:
        rationale = rationale[:2497] + "..."
    ticker = f" ({args.event_ticker})" if getattr(args, "event_ticker", None) else ""
    lines = [
        "ClaudeProphet task succeeded",
        "",
        f"Task/event: {args.event_title}{ticker}",
        f"Request ID: {args.request_id}",
        f"Duration: {duration:.1f}s" if isinstance(duration, (int, float)) else "Duration: not visible",
        f"Forecast source: {forecast_source or 'not visible'}",
        "",
        "Final forecast:",
        *format_probability_rows(prediction),
        "",
        "Market adjustment vs Kalshi:",
        *summarize_market_adjustment(workspace),
    ]
    if rationale:
        lines.extend(["", "Rationale:", rationale])
    evidence = summarize_evidence(workspace / "evidence_manifest.json")
    if evidence:
        lines.extend(["", "Evidence/tools used:", evidence])
    return "\n".join(lines) + "\n"


def summarize_errors(workspace: Path, stage: str | None) -> str:
    parts: list[str] = []
    if stage in FAILURE_OR_DEGRADED_STAGES:
        parts.append(f"terminal stage {stage}")
        include_stderr = True
    elif stage:
        parts.append(f"terminal stage {stage}; no failure/degraded stage visible")
        include_stderr = False
    else:
        parts.append("no terminal stage visible")
        include_stderr = True
    stderr_tail = tail_text(workspace / "claude_stderr.txt", 1200)
    if include_stderr and stderr_tail:
        parts.append("stderr tail: " + stderr_tail.replace("\n", " ")[-1200:])
    return "; ".join(parts)


def tail_text(path: Path, max_chars: int) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    return text[-max_chars:]


def load_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError:
        return None


def send_message(openclaw_bin: str, channel: str, target: str, message: str) -> None:
    subprocess.run(
        [openclaw_bin, "message", "send", "--channel", channel, "--target", target, "--message", message],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def send_success_email(args: argparse.Namespace, workspace: Path, trace: list[dict[str, Any]]) -> None:
    body = compose_success_email(args, workspace, trace)
    subprocess.run(
        [
            "gog",
            "gmail",
            "send",
            "--account",
            args.success_email_account,
            "--to",
            args.success_email_to,
            "--subject",
            f"[ClaudeProphet succeeded] {args.event_title}",
            "--body-file",
            "-",
        ],
        input=body,
        text=True,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def send_failure_email(args: argparse.Namespace, workspace: Path, stage: str | None) -> None:
    body = (
        f"ClaudeProphet request {args.request_id} reached {stage}.\n"
        f"Event: {args.event_title}\n"
        f"Workspace: {workspace}\n"
        f"Error tail: {tail_text(workspace / 'claude_stderr.txt', 2000)}\n"
    )
    subprocess.run(
        [
            "gog",
            "gmail",
            "send",
            "--account",
            args.failure_email_account,
            "--to",
            args.failure_email_to,
            "--subject",
            f"[ClaudeProphet failed] {args.request_id} {args.event_title}",
            "--body",
            body,
        ],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


if __name__ == "__main__":
    raise SystemExit(main())

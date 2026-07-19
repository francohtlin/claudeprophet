from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api_service.run_metadata import append_evidence_item, write_trace
from local_env import load_local_env
from submit_prediction.cli import write_checkpoint
from submit_prediction.validator import validate_submission


DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "openai/gpt-5.5"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="OpenRouter fallback forecast for ClaudeProphet.")
    parser.add_argument("--event", required=True, help="Path to event JSON, or event text.")
    parser.add_argument("--workspace", default=None, help="Request workspace for checkpoints and trace logs.")
    parser.add_argument("--model", default=None, help="OpenRouter model id. Defaults to env or openai/gpt-5.5.")
    parser.add_argument("--timeout-seconds", type=float, default=45.0)
    args = parser.parse_args(argv)

    load_local_env(ROOT / ".env")
    workspace = Path(args.workspace) if args.workspace else None
    event_path = Path(args.event)
    event = load_event(event_path if event_path.exists() else None, args.event)
    model = args.model or os.getenv("OPENROUTER_FALLBACK_MODEL") or DEFAULT_MODEL

    try:
        prediction = run_openrouter_forecast(
            event=event,
            model=model,
            timeout_seconds=args.timeout_seconds,
            workspace=workspace,
        )
        if event_path.exists() and workspace:
            write_checkpoint(
                event_path,
                kind="final",
                prediction=prediction,
                validation=validate_submission(prediction, event=event).to_dict(original_prediction=prediction),
            )
            (workspace / "openrouter_fallback_prediction.json").write_text(
                json.dumps(prediction, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        json.dump(prediction, sys.stdout, separators=(",", ":"))
        sys.stdout.write("\n")
        return 0
    except Exception as exc:
        if workspace:
            write_trace(
                work_dir=workspace,
                request_id=workspace.name,
                stage="openrouter_fallback_failed",
                payload={"error": sanitize_error(str(exc)), "model": model},
            )
        print(f"OpenRouter fallback failed: {sanitize_error(str(exc))}", file=sys.stderr)
        return 1


def run_openrouter_forecast(
    *,
    event: dict[str, Any],
    model: str,
    timeout_seconds: float,
    workspace: Path | None,
) -> dict[str, Any]:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not configured.")

    if workspace:
        write_trace(
            work_dir=workspace,
            request_id=workspace.name,
            stage="openrouter_fallback_started",
            payload={"model": model, "timeout_seconds": timeout_seconds},
        )

    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a fallback forecasting model for Prophet Hacks. "
                    "Return strict JSON only with probabilities and rationale. "
                    "Use every event outcome exactly once. Keep the rationale concise."
                ),
            },
            {"role": "user", "content": build_prompt(event)},
        ],
        "temperature": 0.2,
        "max_tokens": 900,
        "response_format": {"type": "json_object"},
    }
    response = post_openrouter(payload, api_key=api_key, timeout_seconds=timeout_seconds)
    content = response_content(response)
    prediction = parse_prediction_json(content)
    validation = validate_submission(prediction, event=event, require_probability_sum=False)
    if not validation.valid:
        raise ValueError("OpenRouter returned invalid forecast JSON: " + "; ".join(validation.errors))
    normalized = validation.normalized_prediction or prediction

    if workspace:
        write_trace(
            work_dir=workspace,
            request_id=workspace.name,
            stage="openrouter_fallback_succeeded",
            payload={"model": model, "validation_warnings": validation.warnings},
        )
        append_evidence_item(
            work_dir=workspace,
            item={
                "kind": "llm_fallback",
                "source": "openrouter",
                "query": model,
                "notes": "OpenRouter fallback produced the final forecast after Claude exited nonzero.",
            },
        )
    return normalized


def build_prompt(event: dict[str, Any]) -> str:
    outcomes = event_outcomes(event)
    return (
        "Forecast this event. Return only JSON in the shape "
        '{"probabilities":[{"market":"<outcome>","probability":0.5}],"rationale":"..."}.\n'
        f"Outcomes to include exactly once: {json.dumps(outcomes)}\n"
        "Do not use yes/no labels unless those are the actual outcomes.\n"
        "If evidence is thin, use a calibrated prior rather than failing.\n\n"
        f"Event JSON:\n{json.dumps(event, indent=2, sort_keys=True)}"
    )


def post_openrouter(payload: dict[str, Any], *, api_key: str, timeout_seconds: float) -> dict[str, Any]:
    base_url = os.getenv("OPENROUTER_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    request = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": os.getenv("OPENROUTER_HTTP_REFERER", "https://predict.hansonwen.dev"),
            "X-Title": os.getenv("OPENROUTER_APP_TITLE", "ClaudeProphet"),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenRouter HTTP {exc.code}: {body[:500]}") from exc


def response_content(response: dict[str, Any]) -> str:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("OpenRouter response did not contain choices.")
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [part.get("text") for part in content if isinstance(part, dict) and isinstance(part.get("text"), str)]
        if parts:
            return "\n".join(parts)
    raise ValueError("OpenRouter response did not contain text content.")


def parse_prediction_json(text: str) -> dict[str, Any]:
    stripped = text.strip()
    candidates = [stripped]
    first = stripped.find("{")
    last = stripped.rfind("}")
    if first != -1 and last != -1 and last > first:
        candidates.append(stripped[first : last + 1])
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", stripped):
        try:
            _, end = decoder.raw_decode(stripped[match.start() :])
        except json.JSONDecodeError:
            continue
        candidates.append(stripped[match.start() : match.start() + end])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            if isinstance(parsed.get("prediction"), dict):
                return parsed["prediction"]
            return parsed
    raise ValueError("Could not parse JSON forecast from OpenRouter response.")


def load_event(path: Path | None, fallback_text: str) -> dict[str, Any]:
    if path is not None:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            return payload
    return {"title": fallback_text, "outcomes": ["Yes", "No"]}


def event_outcomes(event: dict[str, Any]) -> list[str]:
    raw = event.get("outcomes") or event.get("markets")
    if isinstance(raw, list):
        outcomes = [item.strip() for item in raw if isinstance(item, str) and item.strip()]
        if outcomes:
            return outcomes
    return ["Yes", "No"]


def sanitize_error(text: str) -> str:
    text = re.sub(r"sk-or-v1-[A-Za-z0-9_-]+", "[REDACTED_OPENROUTER_KEY]", text)
    text = re.sub(r"sk-proj-[A-Za-z0-9_-]+", "[REDACTED_OPENAI_KEY]", text)
    return text


if __name__ == "__main__":
    raise SystemExit(main())

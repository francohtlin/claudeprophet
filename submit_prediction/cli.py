from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from submit_prediction.validator import validate_submission


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="submit_prediction",
        description=(
            "Validation-only Prophet Hacks final prediction tool. "
            "It checks schema/rule conformance and never submits externally."
        ),
    )
    parser.add_argument(
        "--event",
        help="Path to the event JSON file. If omitted, event outcome labels cannot be fully checked.",
    )
    parser.add_argument(
        "--prediction",
        help="Path to the prediction JSON file. If omitted, JSON is read from stdin.",
    )
    parser.add_argument(
        "--no-require-rationale",
        action="store_true",
        help="Warn instead of failing when rationale is missing.",
    )
    parser.add_argument(
        "--require-probability-sum",
        action="store_true",
        help="Fail unless probabilities sum to exactly 1 within a small tolerance.",
    )
    parser.add_argument(
        "--kind",
        choices=["validate", "initial", "final"],
        default="validate",
        help=(
            "validate only, or checkpoint a valid initial/final candidate beside the event file. "
            "Checkpointing never submits externally."
        ),
    )
    args = parser.parse_args(argv)

    try:
        event = _load_json(Path(args.event)) if args.event else None
        prediction_payload = (
            _load_json(Path(args.prediction)) if args.prediction else _load_stdin_json()
        )
        prediction, embedded_event = _split_prediction_payload(prediction_payload)
        if event is None:
            event = embedded_event
    except ValueError as exc:
        json.dump(
            {
                "tool": "Submit prediction",
                "valid": False,
                "submitted": False,
                "message": "Prediction failed Prophet Hacks schema validation.",
                "errors": [str(exc)],
                "warnings": [],
            },
            sys.stdout,
            indent=2,
            sort_keys=False,
        )
        sys.stdout.write("\n")
        return 1

    result = validate_submission(
        prediction,
        event=event,
        require_rationale=not args.no_require_rationale,
        require_probability_sum=args.require_probability_sum,
    )
    if args.kind != "validate":
        result.submitted = False
        if result.valid:
            if not args.event:
                result.errors.append("--kind initial/final requires --event so the checkpoint location is known.")
                result.valid = False
                result.message = "Prediction failed Prophet Hacks schema validation."
            else:
                checkpoint_path = write_checkpoint(
                    Path(args.event),
                    kind=args.kind,
                    prediction=result.normalized_prediction or prediction,
                    validation=result.to_dict(original_prediction=prediction),
                )
                result.message = (
                    f"Valid {args.kind} forecast checkpoint saved to {checkpoint_path}. "
                    "No external submission was performed."
                )
    json.dump(result.to_dict(original_prediction=prediction), sys.stdout, indent=2, sort_keys=False)
    sys.stdout.write("\n")
    return 0 if result.valid else 1


def write_checkpoint(
    event_path: Path,
    *,
    kind: str,
    prediction: dict[str, Any],
    validation: dict[str, Any],
) -> Path:
    output_path = event_path.with_name(f"{kind}_submission.json")
    payload = {
        "kind": kind,
        "prediction": prediction,
        "validation": validation,
    }
    tmp = output_path.with_suffix(output_path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(output_path)
    return output_path


def _load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except OSError as exc:
        raise ValueError(f"Could not read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} is not valid JSON: {exc}") from exc


def _load_stdin_json() -> Any:
    raw = sys.stdin.read()
    if not raw.strip():
        raise ValueError("No prediction JSON provided on stdin.")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"stdin is not valid JSON: {exc}") from exc


def _split_prediction_payload(payload: Any) -> tuple[Any, dict[str, Any] | None]:
    if not isinstance(payload, dict):
        return payload, None

    if "prediction" not in payload:
        return payload, None

    event = payload.get("event")
    if event is not None and not isinstance(event, dict):
        raise ValueError("stdin event must be a JSON object when provided.")

    return payload["prediction"], event

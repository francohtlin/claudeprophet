from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite
from typing import Any


DEFAULT_BINARY_OUTCOMES = ("Yes", "No")
SUM_TOLERANCE = 1e-6


@dataclass
class ValidationResult:
    valid: bool
    message: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    normalized_prediction: dict[str, Any] | None = None
    submitted: bool = False

    def to_dict(self, original_prediction: Any | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "tool": "Submit prediction",
            "valid": self.valid,
            "submitted": self.submitted,
            "message": self.message,
            "errors": self.errors,
            "warnings": self.warnings,
        }
        if self.normalized_prediction is not None:
            payload["normalized_prediction"] = self.normalized_prediction
        if not self.valid:
            payload["failed_output"] = original_prediction
        return payload


def validate_submission(
    prediction: Any,
    *,
    event: dict[str, Any] | None = None,
    require_rationale: bool = True,
    require_probability_sum: bool = False,
) -> ValidationResult:
    """Validate a Prophet Hacks forecast response without submitting it.

    The preferred hackathon response shape is:

    {
      "probabilities": [
        {"market": "Yes", "probability": 0.57},
        {"market": "No", "probability": 0.43}
      ],
      "rationale": "Brief evidence and calibration note."
    }

    Legacy {"p_yes": 0.57, "rationale": "..."} responses are accepted only
    for binary events and are normalized to the preferred probability list.
    """
    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(prediction, dict):
        return ValidationResult(
            valid=False,
            message="Prediction must be a JSON object.",
            errors=["Prediction root is not an object."],
        )

    event_outcomes = _event_outcomes(event)
    normalized: dict[str, Any] | None = None

    has_probabilities = "probabilities" in prediction
    has_p_yes = "p_yes" in prediction

    if has_probabilities and has_p_yes:
        warnings.append(
            "Both probabilities and p_yes were provided; probabilities will be validated as authoritative."
        )

    if has_probabilities:
        entries = prediction["probabilities"]
        if isinstance(entries, dict):
            errors.append(
                "probabilities must be a list of {market, probability} objects, not a mapping."
            )
            probability_rows: list[dict[str, Any]] | None = None
        elif isinstance(entries, list):
            probability_rows = entries
        else:
            errors.append("probabilities must be a list.")
            probability_rows = None

        parsed_rows = (
            _validate_probability_rows(probability_rows, errors)
            if probability_rows is not None
            else []
        )
        if parsed_rows:
            _validate_markets(parsed_rows, event_outcomes, errors, warnings)
            _validate_probability_sum(
                parsed_rows,
                errors,
                warnings,
                require_probability_sum=require_probability_sum,
            )
            normalized = {
                "probabilities": [
                    {"market": market, "probability": probability}
                    for market, probability in parsed_rows
                ]
            }
    elif has_p_yes:
        p_yes = prediction["p_yes"]
        if not _is_number(p_yes):
            errors.append("p_yes must be a finite number between 0 and 1.")
        elif not 0 <= float(p_yes) <= 1:
            errors.append(f"p_yes must be between 0 and 1; got {p_yes!r}.")
        elif event_outcomes and not _is_binary_outcome_set(event_outcomes):
            errors.append(
                "p_yes is only valid for binary Yes/No events; this event has "
                f"{len(event_outcomes)} outcomes."
            )
        else:
            yes_probability = float(p_yes)
            no_probability = round(1.0 - yes_probability, 12)
            normalized = {
                "probabilities": [
                    {"market": "Yes", "probability": yes_probability},
                    {"market": "No", "probability": no_probability},
                ]
            }
            warnings.append(
                "Legacy p_yes format is accepted for binary events, but the preferred final schema is probabilities."
            )
    else:
        errors.append("Prediction must include probabilities or legacy p_yes.")

    rationale = prediction.get("rationale")
    if rationale is None:
        if require_rationale:
            errors.append("Missing required rationale string.")
        else:
            warnings.append("Missing rationale string.")
    elif not isinstance(rationale, str):
        errors.append("rationale must be a string.")
    elif not rationale.strip():
        errors.append("rationale must not be empty.")
    elif normalized is not None:
        normalized["rationale"] = rationale.strip()

    extra_keys = sorted(set(prediction) - {"probabilities", "p_yes", "rationale"})
    if extra_keys:
        warnings.append(
            "Unexpected top-level keys are ignored by the validator: "
            + ", ".join(extra_keys)
            + "."
        )

    if errors:
        return ValidationResult(
            valid=False,
            message="Prediction failed Prophet Hacks schema validation.",
            errors=errors,
            warnings=warnings,
            normalized_prediction=normalized,
        )

    return ValidationResult(
        valid=True,
        message=(
            "Prediction conforms to the Prophet Hacks forecast schema. "
            "No external submission was performed."
        ),
        warnings=warnings,
        normalized_prediction=normalized,
    )


def _validate_probability_rows(
    rows: list[Any],
    errors: list[str],
) -> list[tuple[str, float]]:
    parsed: list[tuple[str, float]] = []
    seen: set[str] = set()

    if not rows:
        errors.append("probabilities must contain at least one market probability.")
        return parsed

    for index, row in enumerate(rows):
        label = f"probabilities[{index}]"
        if not isinstance(row, dict):
            errors.append(f"{label} must be an object.")
            continue

        market = row.get("market")
        probability = row.get("probability")

        if not isinstance(market, str) or not market.strip():
            errors.append(f"{label}.market must be a non-empty string.")
            continue
        market = market.strip()

        if market in seen:
            errors.append(f"Duplicate market in probabilities: {market!r}.")
            continue
        seen.add(market)

        if not _is_number(probability):
            errors.append(f"{label}.probability must be a finite number.")
            continue
        probability_float = float(probability)

        if not 0 <= probability_float <= 1:
            errors.append(
                f"{label}.probability must be between 0 and 1; got {probability!r}."
            )
            continue

        parsed.append((market, probability_float))

    return parsed


def _validate_markets(
    rows: list[tuple[str, float]],
    event_outcomes: list[str],
    errors: list[str],
    warnings: list[str],
) -> None:
    if not event_outcomes:
        submitted_markets = [market for market, _ in rows]
        if _is_binary_outcome_set(submitted_markets):
            return
        if len(submitted_markets) == 1:
            errors.append(
                "Only one probability was provided and the event has no outcomes to verify against. "
                "Use legacy p_yes for binary events or provide both Yes and No probabilities."
            )
            return
        warnings.append(
            "Event did not provide outcomes, so market labels could not be checked against the event."
        )
        return

    submitted = [market for market, _ in rows]
    submitted_set = set(submitted)
    expected_set = set(event_outcomes)

    missing = [outcome for outcome in event_outcomes if outcome not in submitted_set]
    extra = [market for market in submitted if market not in expected_set]

    if missing:
        errors.append(
            "Missing probabilities for event outcomes: "
            + ", ".join(repr(item) for item in missing)
            + "."
        )
    if extra:
        errors.append(
            "Predicted markets not present in event outcomes: "
            + ", ".join(repr(item) for item in extra)
            + "."
        )


def _validate_probability_sum(
    rows: list[tuple[str, float]],
    errors: list[str],
    warnings: list[str],
    *,
    require_probability_sum: bool,
) -> None:
    total = sum(probability for _, probability in rows)
    if total <= 0:
        errors.append("Probability total must be greater than 0.")
        return

    if abs(total - 1.0) > SUM_TOLERANCE:
        message = f"Probability total is {total:.12g}, not 1.0."
        if require_probability_sum:
            errors.append(message)
        else:
            warnings.append(
                message
                + " This is allowed for non-mutually-exclusive/top-K events; for mutually exclusive outcomes, summing near 1 is still preferred."
            )


def _event_outcomes(event: dict[str, Any] | None) -> list[str]:
    if not event:
        return []

    raw_outcomes = event.get("outcomes")
    if isinstance(raw_outcomes, list):
        outcomes = [item.strip() for item in raw_outcomes if isinstance(item, str) and item.strip()]
        if outcomes:
            return outcomes

    return []


def _is_binary_outcome_set(outcomes: list[str]) -> bool:
    normalized = {outcome.casefold() for outcome in outcomes}
    return normalized == {item.casefold() for item in DEFAULT_BINARY_OUTCOMES}


def _is_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return isfinite(float(value))

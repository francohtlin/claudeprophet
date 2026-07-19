from submit_prediction.validator import validate_submission
from submit_prediction.cli import main


def test_valid_multi_outcome_prediction() -> None:
    event = {"outcomes": ["Pittsburgh", "Atlanta"]}
    prediction = {
        "probabilities": [
            {"market": "Pittsburgh", "probability": 0.68},
            {"market": "Atlanta", "probability": 0.32},
        ],
        "rationale": "Pittsburgh has the stronger market and roster signal.",
    }

    result = validate_submission(prediction, event=event)

    assert result.valid
    assert result.normalized_prediction == prediction


def test_rejects_missing_event_outcome() -> None:
    event = {"outcomes": ["Pittsburgh", "Atlanta"]}
    prediction = {
        "probabilities": [{"market": "Pittsburgh", "probability": 0.68}],
        "rationale": "Incomplete distribution.",
    }

    result = validate_submission(prediction, event=event)

    assert not result.valid
    assert any("Missing probabilities" in error for error in result.errors)


def test_rejects_extra_market() -> None:
    event = {"outcomes": ["Pittsburgh", "Atlanta"]}
    prediction = {
        "probabilities": [
            {"market": "Pittsburgh", "probability": 0.68},
            {"market": "Atlanta", "probability": 0.22},
            {"market": "Tie", "probability": 0.10},
        ],
        "rationale": "Invented an unavailable outcome.",
    }

    result = validate_submission(prediction, event=event)

    assert not result.valid
    assert any("not present in event outcomes" in error for error in result.errors)


def test_rejects_probability_mapping_shape() -> None:
    prediction = {
        "probabilities": {"Yes": 0.57, "No": 0.43},
        "rationale": "Wrong schema shape.",
    }

    result = validate_submission(prediction)

    assert not result.valid
    assert any("not a mapping" in error for error in result.errors)


def test_accepts_legacy_p_yes_for_binary_event() -> None:
    event = {"outcomes": ["Yes", "No"]}
    prediction = {"p_yes": 0.57, "rationale": "Binary legacy output."}

    result = validate_submission(prediction, event=event)

    assert result.valid
    assert result.normalized_prediction == {
        "probabilities": [
            {"market": "Yes", "probability": 0.57},
            {"market": "No", "probability": 0.43},
        ],
        "rationale": "Binary legacy output.",
    }


def test_rejects_legacy_p_yes_for_multi_outcome_event() -> None:
    event = {"outcomes": ["A", "B", "C"]}
    prediction = {"p_yes": 0.57, "rationale": "Not enough labels."}

    result = validate_submission(prediction, event=event)

    assert not result.valid
    assert any("only valid for binary" in error for error in result.errors)


def test_probability_sum_warning_by_default() -> None:
    event = {"outcomes": ["A", "B"]}
    prediction = {
        "probabilities": [
            {"market": "A", "probability": 0.7},
            {"market": "B", "probability": 0.2},
        ],
        "rationale": "Valid but not normalized.",
    }

    result = validate_submission(prediction, event=event)

    assert result.valid
    assert any("not 1.0" in warning for warning in result.warnings)
    assert any("top-K" in warning for warning in result.warnings)


def test_accepts_top_k_style_probabilities_that_sum_above_one() -> None:
    event = {"outcomes": ["A", "B", "C", "D"]}
    prediction = {
        "probabilities": [
            {"market": "A", "probability": 0.75},
            {"market": "B", "probability": 0.70},
            {"market": "C", "probability": 0.35},
            {"market": "D", "probability": 0.20},
        ],
        "rationale": "Top-2 style inclusion probabilities.",
    }

    result = validate_submission(prediction, event=event)

    assert result.valid
    assert any("non-mutually-exclusive" in warning for warning in result.warnings)


def test_probability_sum_can_be_strict() -> None:
    event = {"outcomes": ["A", "B"]}
    prediction = {
        "probabilities": [
            {"market": "A", "probability": 0.7},
            {"market": "B", "probability": 0.2},
        ],
        "rationale": "Invalid in strict mode.",
    }

    result = validate_submission(prediction, event=event, require_probability_sum=True)

    assert not result.valid
    assert any("not 1.0" in error for error in result.errors)


def test_rejects_single_probability_when_event_outcomes_are_unknown() -> None:
    prediction = {
        "probabilities": [{"market": "Yes", "probability": 0.57}],
        "rationale": "This omits the No side.",
    }

    result = validate_submission(prediction)

    assert not result.valid
    assert any("Only one probability" in error for error in result.errors)


def test_submit_tool_writes_initial_checkpoint(tmp_path) -> None:
    event_path = tmp_path / "event.json"
    prediction_path = tmp_path / "prediction.json"
    event_path.write_text('{"outcomes":["A","B"]}', encoding="utf-8")
    prediction_path.write_text(
        '{"probabilities":[{"market":"A","probability":0.6},{"market":"B","probability":0.4}],"rationale":"Initial."}',
        encoding="utf-8",
    )

    code = main(["--kind", "initial", "--event", str(event_path), "--prediction", str(prediction_path)])

    assert code == 0
    assert (tmp_path / "initial_submission.json").exists()

import json
from pathlib import Path

import scripts.openrouter_fallback as openrouter_fallback


def test_parse_prediction_json_extracts_embedded_object() -> None:
    parsed = openrouter_fallback.parse_prediction_json(
        'Forecast:\n```json\n{"probabilities":[{"market":"Yes","probability":0.7},{"market":"No","probability":0.3}],"rationale":"Test."}\n```'
    )

    assert parsed["probabilities"][0]["market"] == "Yes"


def test_openrouter_fallback_writes_valid_final_checkpoint(tmp_path, monkeypatch) -> None:
    event_path = tmp_path / "event.json"
    event_path.write_text(
        json.dumps({"title": "Will X happen?", "outcomes": ["A", "B"]}),
        encoding="utf-8",
    )

    def fake_post_openrouter(payload, *, api_key, timeout_seconds):
        assert api_key == "sk-or-v1-test"
        assert payload["model"] == "openai/gpt-5.5"
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "probabilities": [
                                    {"market": "A", "probability": 0.62},
                                    {"market": "B", "probability": 0.38},
                                ],
                                "rationale": "OpenRouter fallback test.",
                            }
                        )
                    }
                }
            ]
        }

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    monkeypatch.setattr(openrouter_fallback, "post_openrouter", fake_post_openrouter)

    exit_code = openrouter_fallback.main(
        [
            "--event",
            str(event_path),
            "--workspace",
            str(tmp_path),
            "--model",
            "openai/gpt-5.5",
        ]
    )

    assert exit_code == 0
    checkpoint = json.loads((tmp_path / "final_submission.json").read_text(encoding="utf-8"))
    assert checkpoint["prediction"]["probabilities"] == [
        {"market": "A", "probability": 0.62},
        {"market": "B", "probability": 0.38},
    ]
    trace_stages = [json.loads(line)["stage"] for line in (tmp_path / "trace.jsonl").read_text().splitlines()]
    assert "openrouter_fallback_started" in trace_stages
    assert "openrouter_fallback_succeeded" in trace_stages

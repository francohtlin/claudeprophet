import json

from fastapi.testclient import TestClient

import api_service.app as service_app
from api_service.app import fallback_prediction, latest_checkpoint_prediction, run_claude_forecast
from submit_prediction.validator import validate_submission


def test_fallback_uses_embedded_market_data() -> None:
    event = {
        "outcomes": ["A", "B"],
        "market_data": {
            "A": {"yes_bid": 60, "yes_ask": 70},
            "B": {"yes_bid": 30, "yes_ask": 40},
        },
    }

    prediction = fallback_prediction(event, reason="test")

    assert prediction["probabilities"] == [
        {"market": "A", "probability": 0.65},
        {"market": "B", "probability": 0.35},
    ]
    assert validate_submission(prediction, event=event).valid


def test_fallback_top_k_prior_can_sum_to_k() -> None:
    event = {
        "title": "Which 5 of these 10 teams will finish top 5?",
        "outcomes": [str(index) for index in range(10)],
    }

    prediction = fallback_prediction(event, reason="test")

    assert sum(row["probability"] for row in prediction["probabilities"]) == 5.0
    assert validate_submission(prediction, event=event).valid


def test_latest_checkpoint_prefers_final_over_initial(tmp_path) -> None:
    event = {"outcomes": ["A", "B"]}
    (tmp_path / "initial_submission.json").write_text(
        json.dumps(
            {
                "kind": "initial",
                "prediction": {
                    "probabilities": [
                        {"market": "A", "probability": 0.55},
                        {"market": "B", "probability": 0.45},
                    ],
                    "rationale": "Initial.",
                },
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "final_submission.json").write_text(
        json.dumps(
            {
                "kind": "final",
                "prediction": {
                    "probabilities": [
                        {"market": "A", "probability": 0.7},
                        {"market": "B", "probability": 0.3},
                    ],
                    "rationale": "Final.",
                },
            }
        ),
        encoding="utf-8",
    )

    prediction = latest_checkpoint_prediction(tmp_path, event)

    assert prediction["_checkpoint_kind"] == "final_checkpoint"
    assert prediction["probabilities"][0]["probability"] == 0.7


def test_latest_checkpoint_uses_initial_when_final_missing(tmp_path) -> None:
    event = {"outcomes": ["A", "B"]}
    (tmp_path / "initial_submission.json").write_text(
        json.dumps(
            {
                "kind": "initial",
                "prediction": {
                    "probabilities": [
                        {"market": "A", "probability": 0.55},
                        {"market": "B", "probability": 0.45},
                    ],
                    "rationale": "Initial.",
                },
            }
        ),
        encoding="utf-8",
    )

    prediction = latest_checkpoint_prediction(tmp_path, event)

    assert prediction["_checkpoint_kind"] == "initial_checkpoint"
    assert prediction["probabilities"][0]["probability"] == 0.55


def test_predict_route_writes_variant_trace_and_evidence_manifest(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(service_app, "TMP_DIR", tmp_path / "api")
    monkeypatch.setattr(service_app, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(service_app, "PREDICTION_LOG", tmp_path / "logs" / "api_predictions.jsonl")
    monkeypatch.setattr(service_app, "AUDIT_JSONL", tmp_path / "logs" / "prediction_audit.jsonl")
    monkeypatch.setattr(service_app, "AUDIT_MARKDOWN", tmp_path / "logs" / "prediction_audit.md")
    monkeypatch.setattr(service_app, "TRACE_LOG_DIR", tmp_path / "logs" / "traces")

    def fake_run_claude_forecast(event_path, *, request_id=None, variant=None):
        assert request_id is not None
        assert variant["variant_id"] == "v1_market_prior_claude"
        assert (event_path.parent / "evidence_manifest.json").exists()
        assert (event_path.parent / "trace.jsonl").exists()
        return {
            "probabilities": [
                {"market": "Yes", "probability": 0.6},
                {"market": "No", "probability": 0.4},
            ],
            "rationale": "Mocked forecast.",
        }

    monkeypatch.setattr(service_app, "run_claude_forecast", fake_run_claude_forecast)

    client = TestClient(service_app.app)
    response = client.post(
        "/predict",
        json={"title": "Will X happen?", "outcomes": ["Yes", "No"]},
    )

    assert response.status_code == 200
    assert response.json()["probabilities"][0]["probability"] == 0.6

    workspaces = list((tmp_path / "api").iterdir())
    assert len(workspaces) == 1
    manifest = json.loads((workspaces[0] / "evidence_manifest.json").read_text(encoding="utf-8"))
    trace_lines = [
        json.loads(line)
        for line in (workspaces[0] / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    log_entry = json.loads((tmp_path / "logs" / "api_predictions.jsonl").read_text(encoding="utf-8").splitlines()[0])

    assert manifest["variant"]["variant_id"] == "v1_market_prior_claude"
    assert manifest["items"][0]["kind"] == "event_payload"
    assert {line["stage"] for line in trace_lines} >= {"request_received", "prediction_validated"}
    assert log_entry["variant"]["variant_id"] == "v1_market_prior_claude"
    assert log_entry["evidence_manifest_path"].endswith("evidence_manifest.json")


def test_predict_route_captures_kalshi_event_snapshot(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(service_app, "TMP_DIR", tmp_path / "api")
    monkeypatch.setattr(service_app, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(service_app, "PREDICTION_LOG", tmp_path / "logs" / "api_predictions.jsonl")
    monkeypatch.setattr(service_app, "AUDIT_JSONL", tmp_path / "logs" / "prediction_audit.jsonl")
    monkeypatch.setattr(service_app, "AUDIT_MARKDOWN", tmp_path / "logs" / "prediction_audit.md")
    monkeypatch.setattr(service_app, "TRACE_LOG_DIR", tmp_path / "logs" / "traces")

    def fake_fetch_kalshi_event_snapshot(ticker):
        assert ticker == "KXTEST-26MAY"
        return {
            "event": {
                "event_ticker": "KXTEST-26MAY",
                "mutually_exclusive": False,
                "title": "Test threshold ladder",
            },
            "markets": [
                {
                    "ticker": "KXTEST-26MAY-100",
                    "yes_sub_title": "At least 100",
                    "yes_bid_dollars": "0.6000",
                    "yes_ask_dollars": "0.7000",
                    "status": "open",
                    "result": "",
                    "volume_fp": "123.0",
                    "open_interest_fp": "45.0",
                },
                {
                    "ticker": "KXTEST-26MAY-110",
                    "yes_sub_title": "At least 110",
                    "yes_bid_dollars": "0.3000",
                    "yes_ask_dollars": "0.4000",
                    "status": "open",
                    "result": "",
                    "volume_fp": "50.0",
                    "open_interest_fp": "12.0",
                },
            ],
        }

    def fake_run_claude_forecast(event_path, *, request_id=None, variant=None):
        snapshot = json.loads((event_path.parent / "kalshi_event_snapshot.json").read_text(encoding="utf-8"))
        assert snapshot["event"]["event_ticker"] == "KXTEST-26MAY"
        return {
            "probabilities": [
                {"market": "At least 100", "probability": 0.65},
                {"market": "At least 110", "probability": 0.35},
            ],
            "rationale": "Mocked forecast.",
        }

    monkeypatch.setattr(service_app, "fetch_kalshi_event_snapshot", fake_fetch_kalshi_event_snapshot)
    monkeypatch.setattr(service_app, "run_claude_forecast", fake_run_claude_forecast)

    client = TestClient(service_app.app)
    response = client.post(
        "/predict",
        json={
            "event_ticker": "KXTEST-26MAY",
            "market_ticker": "KXTEST-26MAY",
            "title": "Test threshold ladder",
            "outcomes": ["At least 100", "At least 110"],
        },
    )

    assert response.status_code == 200

    workspaces = list((tmp_path / "api").iterdir())
    manifest = json.loads((workspaces[0] / "evidence_manifest.json").read_text(encoding="utf-8"))
    trace_stages = {
        json.loads(line)["stage"]
        for line in (workspaces[0] / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    }

    kalshi_items = [item for item in manifest["items"] if item["kind"] == "prediction_market_snapshot"]
    assert len(kalshi_items) == 1
    assert kalshi_items[0]["source"] == "kalshi_event_api"
    assert kalshi_items[0]["ticker"] == "KXTEST-26MAY"
    assert kalshi_items[0]["snapshot_path"].endswith("kalshi_event_snapshot.json")
    assert kalshi_items[0]["summary"]["mutually_exclusive"] is False
    assert kalshi_items[0]["summary"]["markets"][0]["midpoint"] == 0.65
    assert "kalshi_event_snapshot_captured" in trace_stages


def test_predict_route_returns_resolved_kalshi_snapshot_without_claude(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(service_app, "TMP_DIR", tmp_path / "api")
    monkeypatch.setattr(service_app, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(service_app, "PREDICTION_LOG", tmp_path / "logs" / "api_predictions.jsonl")
    monkeypatch.setattr(service_app, "AUDIT_JSONL", tmp_path / "logs" / "prediction_audit.jsonl")
    monkeypatch.setattr(service_app, "AUDIT_MARKDOWN", tmp_path / "logs" / "prediction_audit.md")
    monkeypatch.setattr(service_app, "TRACE_LOG_DIR", tmp_path / "logs" / "traces")

    def fake_fetch_kalshi_event_snapshot(ticker):
        assert ticker == "KXSETTLED-26MAY"
        return {
            "event": {"event_ticker": "KXSETTLED-26MAY", "mutually_exclusive": False},
            "markets": [
                {
                    "ticker": "KXSETTLED-26MAY-100",
                    "yes_sub_title": "At least 100",
                    "status": "finalized",
                    "result": "yes",
                    "settlement_value_dollars": "1.0000",
                },
                {
                    "ticker": "KXSETTLED-26MAY-110",
                    "yes_sub_title": "At least 110",
                    "status": "finalized",
                    "result": "no",
                    "settlement_value_dollars": "0.0000",
                },
            ],
        }

    def fail_run_claude_forecast(event_path, *, request_id=None, variant=None):
        raise AssertionError("resolved snapshot should bypass Claude")

    monkeypatch.setattr(service_app, "fetch_kalshi_event_snapshot", fake_fetch_kalshi_event_snapshot)
    monkeypatch.setattr(service_app, "run_claude_forecast", fail_run_claude_forecast)

    client = TestClient(service_app.app)
    response = client.post(
        "/predict",
        json={
            "event_ticker": "KXSETTLED-26MAY",
            "market_ticker": "KXSETTLED-26MAY",
            "title": "Settled threshold ladder",
            "outcomes": ["At least 100", "At least 110"],
        },
    )

    assert response.status_code == 200
    assert response.json()["probabilities"] == [
        {"market": "At least 100", "probability": 1.0},
        {"market": "At least 110", "probability": 0.0},
    ]
    workspaces = list((tmp_path / "api").iterdir())
    trace_stages = {
        json.loads(line)["stage"]
        for line in (workspaces[0] / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    }
    assert "kalshi_resolved_snapshot_used" in trace_stages


def test_predict_route_fallback_uses_open_kalshi_snapshot_when_claude_fails(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(service_app, "TMP_DIR", tmp_path / "api")
    monkeypatch.setattr(service_app, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(service_app, "PREDICTION_LOG", tmp_path / "logs" / "api_predictions.jsonl")
    monkeypatch.setattr(service_app, "AUDIT_JSONL", tmp_path / "logs" / "prediction_audit.jsonl")
    monkeypatch.setattr(service_app, "AUDIT_MARKDOWN", tmp_path / "logs" / "prediction_audit.md")
    monkeypatch.setattr(service_app, "TRACE_LOG_DIR", tmp_path / "logs" / "traces")

    def fake_fetch_kalshi_event_snapshot(ticker):
        assert ticker == "KXOPEN-26MAY"
        return {
            "event": {"event_ticker": "KXOPEN-26MAY", "mutually_exclusive": False},
            "markets": [
                {
                    "ticker": "KXOPEN-26MAY-100",
                    "yes_sub_title": "At least 100",
                    "yes_bid_dollars": "0.6000",
                    "yes_ask_dollars": "0.7000",
                    "status": "open",
                    "result": "",
                },
                {
                    "ticker": "KXOPEN-26MAY-110",
                    "yes_sub_title": "At least 110",
                    "yes_bid_dollars": "0.3000",
                    "yes_ask_dollars": "0.4000",
                    "status": "open",
                    "result": "",
                },
            ],
        }

    def fail_run_claude_forecast(event_path, *, request_id=None, variant=None):
        raise TimeoutError("forced timeout")

    monkeypatch.setattr(service_app, "fetch_kalshi_event_snapshot", fake_fetch_kalshi_event_snapshot)
    monkeypatch.setattr(service_app, "run_claude_forecast", fail_run_claude_forecast)

    client = TestClient(service_app.app)
    response = client.post(
        "/predict",
        json={
            "event_ticker": "KXOPEN-26MAY",
            "market_ticker": "KXOPEN-26MAY",
            "title": "Open threshold ladder",
            "outcomes": ["At least 100", "At least 110"],
        },
    )

    assert response.status_code == 200
    assert response.json()["probabilities"] == [
        {"market": "At least 100", "probability": 0.65},
        {"market": "At least 110", "probability": 0.35},
    ]


def test_kalshi_event_ticker_derives_event_from_market_ticker() -> None:
    assert (
        service_app.kalshi_event_ticker({"market_ticker": "KXJOBLESSCLAIMS-26MAY21-210000"})
        == "KXJOBLESSCLAIMS-26MAY21"
    )


def test_predict_single_event_wakes_openclaw_observer_once(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(service_app, "TMP_DIR", tmp_path / "api")
    monkeypatch.setattr(service_app, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(service_app, "PREDICTION_LOG", tmp_path / "logs" / "api_predictions.jsonl")
    monkeypatch.setattr(service_app, "AUDIT_JSONL", tmp_path / "logs" / "prediction_audit.jsonl")
    monkeypatch.setattr(service_app, "AUDIT_MARKDOWN", tmp_path / "logs" / "prediction_audit.md")
    monkeypatch.setattr(service_app, "TRACE_LOG_DIR", tmp_path / "logs" / "traces")
    monkeypatch.setenv("CLAUDE_OPENCLAW_OBSERVER_ENABLED", "true")
    monkeypatch.setenv("CLAUDE_OPENCLAW_BIN", "openclaw")
    monkeypatch.setenv("CLAUDE_OPENCLAW_OBSERVER_TO", "8173956648")
    monkeypatch.setenv("CLAUDE_OPENCLAW_OBSERVER_FAILURE_EMAIL_TO", "wenhanson0@gmail.com")
    monkeypatch.setenv("CLAUDE_OPENCLAW_OBSERVER_FAILURE_EMAIL_ACCOUNT", "wenhanson0@gmail.com")
    monkeypatch.setenv("CLAUDE_OPENCLAW_OBSERVER_SUCCESS_EMAIL_TO", "jamesgui@usc.edu")
    monkeypatch.setenv("CLAUDE_OPENCLAW_OBSERVER_SUCCESS_EMAIL_ACCOUNT", "wenhanson0@gmail.com")

    started = []

    class StartedProcess:
        pid = 67890

    def fake_popen(args, **kwargs):
        started.append((args, kwargs))
        return StartedProcess()

    def fake_run_claude_forecast(event_path, *, request_id=None, variant=None):
        return {
            "probabilities": [
                {"market": "Yes", "probability": 0.6},
                {"market": "No", "probability": 0.4},
            ],
            "rationale": "Mocked forecast.",
        }

    monkeypatch.setattr(service_app.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(service_app, "run_claude_forecast", fake_run_claude_forecast)

    prediction = service_app.predict_single_event({"title": "Will X happen?", "outcomes": ["Yes", "No"]})

    assert prediction["probabilities"][0]["probability"] == 0.6
    assert len(started) == 1
    workspaces = list((tmp_path / "api").iterdir())
    assert len(workspaces) == 1
    command = started[0][0]
    assert "python" in command[0]
    assert command[1].endswith("scripts/openclaw_observer.py")
    assert command[command.index("--workspace") + 1].endswith("/api/" + workspaces[0].name)
    assert command[command.index("--delivery-channel") + 1] == "telegram"
    assert command[command.index("--delivery-to") + 1] == "8173956648"
    assert command[command.index("--failure-email-to") + 1] == "wenhanson0@gmail.com"
    assert command[command.index("--failure-email-account") + 1] == "wenhanson0@gmail.com"
    assert command[command.index("--success-email-to") + 1] == "jamesgui@usc.edu"
    assert command[command.index("--success-email-account") + 1] == "wenhanson0@gmail.com"
    assert command[command.index("--event-title") + 1] == "Will X happen?"
    assert "cron" not in command

    assert (workspaces[0] / "openclaw_observer.json").exists()
    trace_stages = {
        json.loads(line)["stage"]
        for line in (workspaces[0] / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    }
    assert {"request_received", "openclaw_observer_started", "prediction_validated"} <= trace_stages


def test_openclaw_observer_start_failure_does_not_fail_prediction(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(service_app, "TMP_DIR", tmp_path / "api")
    monkeypatch.setattr(service_app, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(service_app, "PREDICTION_LOG", tmp_path / "logs" / "api_predictions.jsonl")
    monkeypatch.setattr(service_app, "AUDIT_JSONL", tmp_path / "logs" / "prediction_audit.jsonl")
    monkeypatch.setattr(service_app, "AUDIT_MARKDOWN", tmp_path / "logs" / "prediction_audit.md")
    monkeypatch.setattr(service_app, "TRACE_LOG_DIR", tmp_path / "logs" / "traces")
    monkeypatch.setenv("CLAUDE_OPENCLAW_OBSERVER_ENABLED", "true")

    def fake_popen(args, **kwargs):
        raise OSError("openclaw missing")

    def fake_run_claude_forecast(event_path, *, request_id=None, variant=None):
        return {
            "probabilities": [
                {"market": "Yes", "probability": 0.6},
                {"market": "No", "probability": 0.4},
            ],
            "rationale": "Mocked forecast.",
        }

    monkeypatch.setattr(service_app.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(service_app, "run_claude_forecast", fake_run_claude_forecast)

    prediction = service_app.predict_single_event({"title": "Will X happen?", "outcomes": ["Yes", "No"]})

    assert prediction["probabilities"][0]["probability"] == 0.6
    workspaces = list((tmp_path / "api").iterdir())
    trace_stages = {
        json.loads(line)["stage"]
        for line in (workspaces[0] / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    }
    assert "openclaw_observer_start_failed" in trace_stages
    assert "prediction_validated" in trace_stages


def test_predict_route_rejects_batch_payloads() -> None:
    client = TestClient(service_app.app)
    response = client.post(
        "/predict",
        json=[
            {
                "event_ticker": "BATCH-1",
                "market_ticker": "BATCH-1-YES",
                "title": "Will X happen?",
                "outcomes": ["Yes", "No"],
            }
        ],
    )

    assert response.status_code == 400
    assert "single event JSON object" in response.json()["detail"]


def test_health_reports_active_forecasts() -> None:
    client = TestClient(service_app.app)

    assert client.get("/health").json()["active_forecasts"] == 0

    service_app.increment_active_forecasts()
    try:
        assert client.get("/health").json()["active_forecasts"] == 1
    finally:
        service_app.decrement_active_forecasts()

    assert client.get("/health").json()["active_forecasts"] == 0


def test_predict_single_event_reports_claude_failure_issue(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(service_app, "TMP_DIR", tmp_path / "api")
    monkeypatch.setattr(service_app, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(service_app, "PREDICTION_LOG", tmp_path / "logs" / "api_predictions.jsonl")
    monkeypatch.setattr(service_app, "AUDIT_JSONL", tmp_path / "logs" / "prediction_audit.jsonl")
    monkeypatch.setattr(service_app, "AUDIT_MARKDOWN", tmp_path / "logs" / "prediction_audit.md")
    monkeypatch.setattr(service_app, "TRACE_LOG_DIR", tmp_path / "logs" / "traces")
    reported = {}

    def fake_run_claude_forecast(event_path, *, request_id=None, variant=None):
        (event_path.parent / "claude_stderr.txt").write_text("market:lookup provider timeout", encoding="utf-8")
        raise RuntimeError("market:lookup provider timeout")

    def fake_report_forecast_issue(*, work_dir, event_path, error, forecast_source):
        reported["work_dir"] = work_dir
        reported["event_path"] = event_path
        reported["error"] = str(error)
        reported["forecast_source"] = forecast_source

    monkeypatch.setattr(service_app, "run_claude_forecast", fake_run_claude_forecast)
    monkeypatch.setattr(service_app, "report_forecast_issue", fake_report_forecast_issue)

    prediction = service_app.predict_single_event({"title": "Will X happen?", "outcomes": ["Yes", "No"]})

    assert prediction["probabilities"] == [
        {"market": "Yes", "probability": 0.5},
        {"market": "No", "probability": 0.5},
    ]
    assert reported["event_path"].name == "event.json"
    assert reported["forecast_source"] == "deterministic_fallback"
    assert "market:lookup provider timeout" in reported["error"]


def test_run_claude_forecast_sets_buffered_agent_time_budget(tmp_path, monkeypatch) -> None:
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps({"outcomes": ["Yes", "No"]}), encoding="utf-8")
    captured_env = {}

    class FinishedProcess:
        pid = 12345

        def __init__(self, *args, **kwargs):
            captured_env.update(kwargs["env"])
            self.stdout = json.dumps(
                {
                    "probabilities": [
                        {"market": "Yes", "probability": 0.55},
                        {"market": "No", "probability": 0.45},
                    ],
                    "rationale": "Finished forecast.",
                }
            )
            self.stderr = ""

        def poll(self):
            return 0

        def communicate(self, timeout=None):
            return self.stdout, self.stderr

    monkeypatch.setenv("CLAUDE_API_TIMEOUT", "540")
    monkeypatch.delenv("CLAUDE_FORECAST_RETURN_BUFFER_SECONDS", raising=False)
    monkeypatch.delenv("CLAUDE_FORECAST_TIME_BUDGET_SECONDS", raising=False)
    monkeypatch.setattr(service_app.subprocess, "Popen", FinishedProcess)

    prediction = run_claude_forecast(event_path, request_id="req-test", variant={})

    assert prediction["probabilities"][0]["market"] == "Yes"
    assert captured_env["CLAUDE_FORECAST_TIME_BUDGET_SECONDS"] == "520"
    assert captured_env["CLAUDE_FORECAST_EVALUATION_TIMEOUT_SECONDS"] == "600"


def test_run_claude_forecast_respects_explicit_agent_time_budget(tmp_path, monkeypatch) -> None:
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps({"outcomes": ["Yes", "No"]}), encoding="utf-8")
    captured_env = {}

    class FinishedProcess:
        pid = 12345

        def __init__(self, *args, **kwargs):
            captured_env.update(kwargs["env"])
            self.stdout = json.dumps(
                {
                    "probabilities": [
                        {"market": "Yes", "probability": 0.55},
                        {"market": "No", "probability": 0.45},
                    ],
                    "rationale": "Finished forecast.",
                }
            )
            self.stderr = ""

        def poll(self):
            return 0

        def communicate(self, timeout=None):
            return self.stdout, self.stderr

    monkeypatch.setenv("CLAUDE_API_TIMEOUT", "85")
    monkeypatch.setenv("CLAUDE_FORECAST_TIME_BUDGET_SECONDS", "42")
    monkeypatch.setattr(service_app.subprocess, "Popen", FinishedProcess)

    prediction = run_claude_forecast(event_path, request_id="req-test", variant={})

    assert prediction["probabilities"][0]["market"] == "Yes"
    assert captured_env["CLAUDE_FORECAST_TIME_BUDGET_SECONDS"] == "42"


def test_run_claude_forecast_returns_initial_checkpoint_on_timeout(tmp_path, monkeypatch) -> None:
    event = {"outcomes": ["Yes", "No"]}
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(event), encoding="utf-8")
    (tmp_path / "initial_submission.json").write_text(
        json.dumps(
            {
                "kind": "initial",
                "prediction": {
                    "probabilities": [
                        {"market": "Yes", "probability": 0.61},
                        {"market": "No", "probability": 0.39},
                    ],
                    "rationale": "Checkpoint fallback.",
                },
            }
        ),
        encoding="utf-8",
    )

    class HangingProcess:
        pid = 12345

        def poll(self):
            return None

    monkeypatch.setenv("CLAUDE_API_TIMEOUT", "1")
    monkeypatch.setattr(service_app.subprocess, "Popen", lambda *args, **kwargs: HangingProcess())
    monkeypatch.setattr(service_app, "stop_process_tree", lambda process, graceful_timeout: ("", "timed out"))

    prediction = run_claude_forecast(event_path, request_id="req-timeout", variant={})

    assert prediction["probabilities"][0]["probability"] == 0.61
    assert prediction["_forecast_source"] == "initial_checkpoint"


def test_run_claude_forecast_allows_final_checkpoint_grace_before_kill(tmp_path, monkeypatch) -> None:
    event = {"outcomes": ["Yes", "No"]}
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(event), encoding="utf-8")
    (tmp_path / "final_submission.json").write_text(
        json.dumps(
            {
                "kind": "final",
                "prediction": {
                    "probabilities": [
                        {"market": "Yes", "probability": 0.7},
                        {"market": "No", "probability": 0.3},
                    ],
                    "rationale": "Final checkpoint.",
                },
            }
        ),
        encoding="utf-8",
    )
    stopped = {"called": False}

    class GracefulProcess:
        pid = 12345

        def __init__(self):
            self.polls = 0

        def poll(self):
            self.polls += 1
            return None if self.polls == 1 else 0

        def communicate(self, timeout=None):
            return "", ""

    monkeypatch.setenv("CLAUDE_API_TIMEOUT", "540")
    monkeypatch.setenv("CLAUDE_FINAL_CHECKPOINT_GRACE_SECONDS", "2")
    monkeypatch.setattr(service_app.subprocess, "Popen", lambda *args, **kwargs: GracefulProcess())
    monkeypatch.setattr(service_app.time, "sleep", lambda seconds: None)

    def fake_stop_process_tree(process, graceful_timeout):
        stopped["called"] = True
        return "", ""

    monkeypatch.setattr(service_app, "stop_process_tree", fake_stop_process_tree)

    prediction = run_claude_forecast(event_path, request_id="req-final", variant={})

    assert prediction["probabilities"][0]["probability"] == 0.7
    assert prediction["_forecast_source"] == "final_checkpoint"
    assert stopped["called"] is False

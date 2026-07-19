import argparse
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("openclaw_observer", ROOT / "scripts" / "openclaw_observer.py")
observer = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(observer)


def test_success_email_is_readable_and_sent_to_configured_recipient(tmp_path, monkeypatch) -> None:
    workspace = tmp_path / "request"
    workspace.mkdir()
    trace = [
        {"stage": "request_received"},
        {
            "stage": "prediction_validated",
            "duration_seconds": 42.5,
            "forecast_source": "final_checkpoint",
        },
    ]
    (workspace / "trace.jsonl").write_text("\n".join(json.dumps(row) for row in trace), encoding="utf-8")
    (workspace / "evidence_manifest.json").write_text(
        json.dumps({"items": [{"kind": "market", "source": "kalshi", "notes": "market prior"}]}),
        encoding="utf-8",
    )
    (workspace / "final_submission.json").write_text(
        json.dumps(
            {
                "prediction": {
                    "probabilities": [
                        {"market": "Yes", "probability": 0.64},
                        {"market": "No", "probability": 0.36},
                    ],
                    "rationale": "Market prior plus evidence favored Yes.",
                }
            }
        ),
        encoding="utf-8",
    )
    args = argparse.Namespace(
        request_id="req-123",
        event_title="Will the task succeed?",
        event_ticker="TASK-YES",
        success_email_to="jamesgui@usc.edu",
        success_email_account="wenhanson0@gmail.com",
    )
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return argparse.Namespace(returncode=0)

    monkeypatch.setattr(observer.subprocess, "run", fake_run)

    observer.send_success_email(args, workspace, trace)

    assert len(calls) == 1
    command, kwargs = calls[0]
    assert command[:4] == ["gog", "gmail", "send", "--account"]
    assert "jamesgui@usc.edu" in command
    assert any(part.startswith("[ClaudeProphet succeeded]") for part in command)
    body = kwargs["input"]
    assert "ClaudeProphet task succeeded" in body
    assert "Will the task succeed? (TASK-YES)" in body
    assert "Request ID: req-123" in body
    assert "Duration: 42.5s" in body
    assert "Final forecast:" in body
    assert "- Yes: 64.0%" in body
    assert "- No: 36.0%" in body
    assert "Rationale:" in body
    assert "Market prior plus evidence favored Yes." in body


def test_observer_report_includes_adjustment_from_event_market_data(tmp_path) -> None:
    workspace = tmp_path / "request"
    workspace.mkdir()
    trace = [
        {"stage": "request_received"},
        {
            "stage": "prediction_validated",
            "duration_seconds": 12.0,
            "forecast_source": "final_checkpoint",
        },
    ]
    (workspace / "event.json").write_text(
        json.dumps(
            {
                "title": "Will the task succeed?",
                "outcomes": ["Yes", "No"],
                "market_data": {
                    "Yes": {"yes_bid": 51, "yes_ask": 53},
                    "No": {"yes_bid": 47, "yes_ask": 49},
                },
            }
        ),
        encoding="utf-8",
    )
    (workspace / "final_submission.json").write_text(
        json.dumps(
            {
                "prediction": {
                    "probabilities": [
                        {"market": "Yes", "probability": 0.64},
                        {"market": "No", "probability": 0.36},
                    ],
                    "rationale": "Market prior plus evidence favored Yes.",
                }
            }
        ),
        encoding="utf-8",
    )

    report = observer.compose_report(
        workspace=workspace,
        request_id="req-123",
        event_title="Will the task succeed?",
        event_ticker="TASK-YES",
        trace=trace,
    )

    assert "Market adjustment vs Kalshi:" in report
    assert "- Yes: forecast 64.0%, Kalshi 52.0%, adjusted +12.0 pp" in report
    assert "- No: forecast 36.0%, Kalshi 48.0%, adjusted -12.0 pp" in report


def test_observer_report_accepts_numeric_event_market_data(tmp_path) -> None:
    workspace = tmp_path / "request"
    workspace.mkdir()
    trace = [
        {"stage": "request_received"},
        {
            "stage": "prediction_validated",
            "duration_seconds": 12.0,
            "forecast_source": "final_checkpoint",
        },
    ]
    (workspace / "event.json").write_text(
        json.dumps(
            {
                "title": "What will CPI be?",
                "outcomes": ["Exactly 4.3%", "Exactly 4.2%"],
                "market_data": {
                    "Exactly 4.3%": 0.41,
                    "Exactly 4.2%": 0.215,
                },
            }
        ),
        encoding="utf-8",
    )
    (workspace / "final_submission.json").write_text(
        json.dumps(
            {
                "prediction": {
                    "probabilities": [
                        {"market": "Exactly 4.3%", "probability": 0.34},
                        {"market": "Exactly 4.2%", "probability": 0.30},
                    ],
                    "rationale": "Normalized the Kalshi row.",
                }
            }
        ),
        encoding="utf-8",
    )

    report = observer.compose_report(
        workspace=workspace,
        request_id="req-123",
        event_title="What will CPI be?",
        event_ticker="CPI",
        trace=trace,
    )

    assert "- Exactly 4.3%: forecast 34.0%, Kalshi 41.0%, adjusted -7.0 pp" in report
    assert "- Exactly 4.2%: forecast 30.0%, Kalshi 21.5%, adjusted +8.5 pp" in report


def test_success_email_includes_adjustment_from_kalshi_evidence(tmp_path) -> None:
    workspace = tmp_path / "request"
    workspace.mkdir()
    trace = [{"stage": "prediction_validated", "duration_seconds": 42.5}]
    (workspace / "evidence_manifest.json").write_text(
        json.dumps(
            {
                "items": [
                    {
                        "kind": "local_tool",
                        "source": "kalshi_discovery",
                        "usable": True,
                        "notes": "Exact Kalshi event lookup returned all five exact markets with current bid/ask/mid: Above 0.0 99/100 mid .995; Above 0.1 98/100 mid .99; Above 0.2 72/79 mid .755; Above 0.3 8/9 mid .085; Above 0.4 0/3 mid .015.",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (workspace / "claude_final.json").write_text(
        json.dumps(
            {
                "probabilities": [
                    {"market": "Above 0.3%", "probability": 0.10},
                    {"market": "Above 0.4%", "probability": 0.015},
                ],
                "rationale": "Market prior plus evidence.",
            }
        ),
        encoding="utf-8",
    )
    args = argparse.Namespace(
        request_id="req-456",
        event_title="Core PCE",
        event_ticker="KXPCECORE",
    )

    body = observer.compose_success_email(args, workspace, trace)

    assert "Market adjustment vs Kalshi:" in body
    assert "- Above 0.3%: forecast 10.0%, Kalshi 8.5%, adjusted +1.5 pp" in body
    assert "- Above 0.4%: forecast 1.5%, Kalshi 1.5%, adjusted +0.0 pp" in body


def test_kalshi_evidence_matches_above_label_to_greater_than_shorthand(tmp_path) -> None:
    workspace = tmp_path / "request"
    workspace.mkdir()
    (workspace / "evidence_manifest.json").write_text(
        json.dumps(
            {
                "items": [
                    {
                        "source": "kalshi_discovery",
                        "usable": True,
                        "notes": "Direct Kalshi event lookup returned all exact markets with current bid/ask/mid: >0.3 8/9 mid .085; >0.4 0/3 mid .015.",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (workspace / "claude_final.json").write_text(
        json.dumps(
            {
                "probabilities": [
                    {"market": "Above 0.3%", "probability": 0.10},
                    {"market": "Above 0.4%", "probability": 0.015},
                ],
                "rationale": "Market prior plus evidence.",
            }
        ),
        encoding="utf-8",
    )

    rows = observer.summarize_market_adjustment(workspace)

    assert "- Above 0.3%: forecast 10.0%, Kalshi 8.5%, adjusted +1.5 pp" in rows
    assert "- Above 0.4%: forecast 1.5%, Kalshi 1.5%, adjusted +0.0 pp" in rows


def test_observer_report_uses_structured_kalshi_snapshot_for_binary_market(tmp_path) -> None:
    workspace = tmp_path / "request"
    workspace.mkdir()
    trace = [
        {"stage": "request_received"},
        {
            "stage": "prediction_validated",
            "duration_seconds": 212.7,
            "forecast_source": "final_checkpoint",
        },
    ]
    (workspace / "event.json").write_text(
        json.dumps(
            {
                "event_ticker": "KXDOTPLOT-26JUN",
                "market_ticker": "KXDOTPLOT-26JUN-3.5",
                "title": "Fed median dot plot in June 2026",
                "outcomes": ["Yes", "No"],
            }
        ),
        encoding="utf-8",
    )
    (workspace / "evidence_manifest.json").write_text(
        json.dumps(
            {
                "items": [
                    {
                        "kind": "prediction_market_snapshot",
                        "source": "kalshi_event_api",
                        "usable": True,
                        "summary": {
                            "event_ticker": "KXDOTPLOT-26JUN",
                            "markets": [
                                {
                                    "ticker": "KXDOTPLOT-26JUN-3.4",
                                    "label": "Above 3.4%",
                                    "last_price": 0.84,
                                    "midpoint": 0.745,
                                    "yes_bid": 0.5,
                                    "yes_ask": 0.99,
                                },
                                {
                                    "ticker": "KXDOTPLOT-26JUN-3.5",
                                    "label": "Above 3.5%",
                                    "last_price": 0.76,
                                    "midpoint": 0.665,
                                    "yes_bid": 0.34,
                                    "yes_ask": 0.99,
                                },
                            ],
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (workspace / "final_submission.json").write_text(
        json.dumps(
            {
                "prediction": {
                    "probabilities": [
                        {"market": "Yes", "probability": 0.80},
                        {"market": "No", "probability": 0.20},
                    ],
                    "rationale": "Anchored on the frozen exact Kalshi ladder.",
                }
            }
        ),
        encoding="utf-8",
    )

    report = observer.compose_report(
        workspace=workspace,
        request_id="req-123",
        event_title="Fed median dot plot in June 2026",
        event_ticker="KXDOTPLOT-26JUN",
        trace=trace,
    )

    assert "- Yes: forecast 80.0%, Kalshi 66.5%, adjusted +13.5 pp" in report
    assert "- No: forecast 20.0%, Kalshi 33.5%, adjusted -13.5 pp" in report


def test_observer_report_prefers_live_bid_ask_midpoint_over_stale_last_trade_for_ladder(tmp_path) -> None:
    workspace = tmp_path / "request"
    workspace.mkdir()
    trace = [
        {"stage": "request_received"},
        {
            "stage": "prediction_validated",
            "duration_seconds": 198.7,
            "forecast_source": "final_checkpoint",
        },
    ]
    (workspace / "evidence_manifest.json").write_text(
        json.dumps(
            {
                "items": [
                    {
                        "kind": "prediction_market_snapshot",
                        "source": "kalshi_event_api",
                        "usable": True,
                        "summary": {
                            "event_ticker": "KXTSAW-26MAY31",
                            "markets": [
                                {
                                    "ticker": "KXTSAW-26MAY31-A2.60",
                                    "label": "Above 2.6 million",
                                    "last_price": 0.36,
                                    "midpoint": 0.40,
                                    "yes_bid": 0.39,
                                    "yes_ask": 0.41,
                                },
                                {
                                    "ticker": "KXTSAW-26MAY31-A2.65",
                                    "label": "Above 2.65 million",
                                    "last_price": 0.03,
                                    "midpoint": 0.025,
                                    "yes_bid": 0.02,
                                    "yes_ask": 0.03,
                                },
                                {
                                    "ticker": "KXTSAW-26MAY31-A2.70",
                                    "label": "Above 2.7 million",
                                    "last_price": 0.04,
                                    "midpoint": 0.01,
                                    "yes_bid": 0.0,
                                    "yes_ask": 0.02,
                                },
                            ],
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (workspace / "final_submission.json").write_text(
        json.dumps(
            {
                "prediction": {
                    "probabilities": [
                        {"market": "Above 2.6 million", "probability": 0.35},
                        {"market": "Above 2.65 million", "probability": 0.045},
                        {"market": "Above 2.7 million", "probability": 0.01},
                    ],
                    "rationale": "Used the frozen Kalshi ladder.",
                }
            }
        ),
        encoding="utf-8",
    )

    report = observer.compose_report(
        workspace=workspace,
        request_id="req-456",
        event_title="TSA avg check-ins from May 25 to 31, 2026?",
        event_ticker="KXTSAW-26MAY31",
        trace=trace,
    )

    assert "- Above 2.6 million: forecast 35.0%, Kalshi 40.0%, adjusted -5.0 pp" in report
    assert "- Above 2.65 million: forecast 4.5%, Kalshi 2.5%, adjusted +2.0 pp" in report
    assert "- Above 2.7 million: forecast 1.0%, Kalshi 1.0%, adjusted +0.0 pp" in report

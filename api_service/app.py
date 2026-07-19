from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from local_env import load_local_env
from api_service.run_metadata import (
    append_evidence_item,
    initialize_request_metadata,
    load_active_variant,
    metadata_paths,
    write_trace,
)
from submit_prediction.validator import validate_submission


ROOT = Path(__file__).resolve().parents[1]
TMP_DIR = ROOT / "tmp" / "api"
LOG_DIR = ROOT / "logs"
PREDICTION_LOG = LOG_DIR / "api_predictions.jsonl"
AUDIT_JSONL = LOG_DIR / "prediction_audit.jsonl"
AUDIT_MARKDOWN = LOG_DIR / "prediction_audit.md"
TRACE_LOG_DIR = LOG_DIR / "traces"
DEFAULT_MODEL = "claude-opus-4-8"
DEFAULT_CLAUDE_SERVICE_TIER = "fast"
DEFAULT_EVALUATION_TIMEOUT_SECONDS = 600.0
DEFAULT_CLAUDE_TIMEOUT_SECONDS = 540.0
DEFAULT_PUBLIC_CLAUDE_TIMEOUT_SECONDS = 60.0
DEFAULT_FORECAST_RETURN_BUFFER_SECONDS = 20.0
DEFAULT_FINAL_CHECKPOINT_GRACE_SECONDS = 20.0
DEFAULT_OPENCLAW_OBSERVER_TIMEOUT_SECONDS = 900
DEFAULT_OPENCLAW_BIN = "/opt/homebrew/bin/openclaw" if Path("/opt/homebrew/bin/openclaw").exists() else "openclaw"
KALSHI_EVENT_SNAPSHOT_NAME = "kalshi_event_snapshot.json"
KALSHI_EVENT_API_BASE = "https://external-api.kalshi.com/trade-api/v2"
_active_forecasts = 0
_active_forecasts_lock = threading.Lock()

load_local_env(ROOT / ".env")

PUBLIC_API_MODE = os.getenv("CLAUDE_PUBLIC_API_MODE", "").lower() in {"1", "true", "yes", "on"}
PUBLIC_PATHS = {"/", "/health", "/predict"}

app = FastAPI(
    title="ClaudeProphet Internal API",
    version="0.1.0",
    description="Local-only API for launching ClaudeProphet forecasts and Prophet server helper calls.",
    docs_url=None if PUBLIC_API_MODE else "/docs",
    redoc_url=None if PUBLIC_API_MODE else "/redoc",
    openapi_url=None if PUBLIC_API_MODE else "/openapi.json",
)


@app.middleware("http")
async def restrict_public_api(request: Request, call_next):
    if PUBLIC_API_MODE and request.url.path not in PUBLIC_PATHS:
        return JSONResponse(status_code=404, content={"detail": "Not found"})
    return await call_next(request)


@app.get("/", response_class=HTMLResponse)
async def public_home() -> str:
    return (ROOT / "api_service" / "public_home.html").read_text(encoding="utf-8")


class RegisterRequest(BaseModel):
    team_name: str
    endpoint_url: str | None = None
    is_active: bool = True
    server_url: str | None = None
    api_key: str | None = Field(default=None, repr=False)


class EndpointRegisterRequest(BaseModel):
    team_name: str
    endpoint_url: str
    is_active: bool = True
    server_url: str | None = None
    api_key: str | None = Field(default=None, repr=False)


@app.get("/health")
async def health() -> dict[str, Any]:
    variant = load_active_variant(ROOT)
    return {
        "status": "ok",
        "service": "claude-prophet-internal-api",
        "generated_at": iso_now(),
        "claude_model": os.getenv("CLAUDE_FORECAST_MODEL", DEFAULT_MODEL),
        "claude_service_tier": os.getenv("CLAUDE_FORECAST_SERVICE_TIER", DEFAULT_CLAUDE_SERVICE_TIER),
        "variant_id": variant.get("variant_id"),
        "prompt_version": variant.get("prompt_version"),
        "calibration_policy": variant.get("calibration_policy"),
        "claude_timeout_seconds": claude_timeout_seconds(),
        "evaluation_timeout_seconds": evaluation_timeout_seconds(),
        "forecast_time_budget_seconds": forecast_time_budget_seconds(
            claude_timeout_seconds(),
            evaluation_timeout_seconds(),
        ),
        "forecast_return_buffer_seconds": forecast_return_buffer_seconds(),
        "active_forecasts": active_forecasts(),
        "prophet_api_configured": bool(resolve_api_key(None, required=False)),
    }


@app.post("/predict")
async def predict(request: Request) -> dict[str, Any]:
    try:
        event = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Request body must be JSON: {exc}") from exc
    if not isinstance(event, dict):
        raise HTTPException(status_code=400, detail="Request body must be a single event JSON object.")

    increment_active_forecasts()
    try:
        return await run_in_threadpool(predict_single_event, event)
    finally:
        decrement_active_forecasts()


def active_forecasts() -> int:
    with _active_forecasts_lock:
        return _active_forecasts


def increment_active_forecasts() -> None:
    global _active_forecasts
    with _active_forecasts_lock:
        _active_forecasts += 1


def decrement_active_forecasts() -> None:
    global _active_forecasts
    with _active_forecasts_lock:
        _active_forecasts = max(0, _active_forecasts - 1)


def predict_single_event(event: dict[str, Any]) -> dict[str, Any]:
    request_id = str(uuid.uuid4())
    started = time.time()
    work_dir = TMP_DIR / request_id
    work_dir.mkdir(parents=True, exist_ok=True)
    event_path = work_dir / "event.json"
    event_path.write_text(json.dumps(event, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    variant = load_active_variant(ROOT)
    initialize_request_metadata(
        work_dir=work_dir,
        request_id=request_id,
        event=event,
        variant=variant,
    )
    write_trace(
        work_dir=work_dir,
        request_id=request_id,
        stage="request_received",
        payload={
            "event": redact(event),
            "variant": redact(variant),
            **metadata_paths(work_dir),
        },
        mirror_dir=TRACE_LOG_DIR,
    )
    append_evidence_item(
        work_dir=work_dir,
        item={
            "kind": "event_payload",
            "source": "prophet_evaluator_request",
            "usable": True,
            "notes": "Original event JSON supplied to /predict. Treat event fields and resolution rules as authoritative.",
        },
    )
    capture_kalshi_event_snapshot(work_dir=work_dir, request_id=request_id, event=event)
    write_prediction_audit(
        {
            "request_id": request_id,
            "stage": "request_received",
            "event": redact(event),
            "variant": redact(variant),
            **metadata_paths(work_dir),
            "received_at": iso_now(),
        }
    )
    failure_issue_reported = False
    resolved_snapshot = kalshi_snapshot_prediction(work_dir, event, require_resolved=True)
    if resolved_snapshot is not None:
        prediction = resolved_snapshot
        forecast_source = prediction.pop("_forecast_source", "kalshi_resolved_snapshot")
        write_trace(
            work_dir=work_dir,
            request_id=request_id,
            stage="kalshi_resolved_snapshot_used",
            payload={
                "forecast_source": forecast_source,
                "duration_seconds": round(time.time() - started, 3),
                "notes": "Captured Kalshi snapshot already contained resolved outcomes, so Claude was bypassed.",
            },
            mirror_dir=TRACE_LOG_DIR,
        )
    else:
        wake_openclaw_observer_once(work_dir=work_dir, request_id=request_id, event=event, variant=variant)
        try:
            prediction = run_claude_forecast(event_path, request_id=request_id, variant=variant)
            forecast_source = prediction.pop("_forecast_source", "claude_stdout") if isinstance(prediction, dict) else "claude_stdout"
        except Exception as exc:
            checkpoint = latest_checkpoint_prediction(work_dir, event)
            snapshot_fallback = None if checkpoint is not None else kalshi_snapshot_prediction(
                work_dir,
                event,
                require_resolved=False,
            )
            fallback = checkpoint or snapshot_fallback or fallback_prediction(event, reason=str(exc))
            source_kind = checkpoint.get("_checkpoint_kind") if isinstance(checkpoint, dict) else None
            if source_kind is None and isinstance(snapshot_fallback, dict):
                source_kind = snapshot_fallback.get("_forecast_source")
            if isinstance(fallback, dict):
                fallback.pop("_checkpoint_kind", None)
                fallback.pop("_forecast_source", None)
            write_trace(
                work_dir=work_dir,
                request_id=request_id,
                stage="fallback_used",
                payload={
                    "error": str(exc),
                    "forecast_source": source_kind or "deterministic_fallback",
                    "duration_seconds": round(time.time() - started, 3),
                },
                mirror_dir=TRACE_LOG_DIR,
            )
            entry = {
                "request_id": request_id,
                "stage": "claude_failed_checkpoint_or_fallback_used",
                "event": redact(event),
                "variant": redact(variant),
                "error": str(exc),
                "final_prediction": redact(fallback),
                "forecast_source": source_kind or "deterministic_fallback",
                **metadata_paths(work_dir),
                "duration_seconds": round(time.time() - started, 3),
                "completed_at": iso_now(),
            }
            write_prediction_log(entry)
            write_prediction_audit(entry)
            fallback_source = source_kind or "deterministic_fallback"
            report_forecast_issue(
                work_dir=work_dir,
                event_path=event_path,
                error=exc,
                forecast_source=fallback_source,
            )
            failure_issue_reported = True
            prediction = fallback
            forecast_source = fallback_source

    validation = validate_submission(prediction, event=event, require_probability_sum=False)
    if not validation.valid:
        write_trace(
            work_dir=work_dir,
            request_id=request_id,
            stage="validation_failed",
            payload={"validation": validation.to_dict(original_prediction=prediction)},
            mirror_dir=TRACE_LOG_DIR,
        )
        entry = {
            "request_id": request_id,
            "stage": "validation_failed",
            "event": redact(event),
            "variant": redact(variant),
            "prediction": redact(prediction),
            "validation": validation.to_dict(original_prediction=prediction),
            **metadata_paths(work_dir),
            "duration_seconds": round(time.time() - started, 3),
            "completed_at": iso_now(),
        }
        write_prediction_log(entry)
        write_prediction_audit(entry)
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Claude returned an invalid prediction.",
                "validation": validation.to_dict(original_prediction=prediction),
            },
        )

    if degraded_forecast_source(forecast_source):
        issue = RuntimeError(f"API returned degraded forecast source: {forecast_source}")
        write_trace(
            work_dir=work_dir,
            request_id=request_id,
            stage="degraded_forecast_returned",
            payload={
                "forecast_source": forecast_source,
                "duration_seconds": round(time.time() - started, 3),
                "reason": "Actual /predict response used a timeout checkpoint or deterministic fallback.",
            },
            mirror_dir=TRACE_LOG_DIR,
        )
        write_prediction_audit(
            {
                "request_id": request_id,
                "stage": "degraded_forecast_returned",
                "event": redact(event),
                "variant": redact(variant),
                "forecast_source": forecast_source,
                "message": "Actual /predict response used a degraded forecast source.",
                **metadata_paths(work_dir),
                "duration_seconds": round(time.time() - started, 3),
                "completed_at": iso_now(),
            }
        )
        if not failure_issue_reported:
            report_forecast_issue(
                work_dir=work_dir,
                event_path=event_path,
                error=issue,
                forecast_source=forecast_source,
            )

    final_prediction = validation.normalized_prediction or prediction
    write_trace(
        work_dir=work_dir,
        request_id=request_id,
        stage="prediction_validated",
        payload={
            "validation_warnings": validation.warnings,
            "forecast_source": forecast_source,
            "duration_seconds": round(time.time() - started, 3),
        },
        mirror_dir=TRACE_LOG_DIR,
    )
    entry = {
        "request_id": request_id,
        "stage": "predict_complete",
        "event": redact(event),
        "variant": redact(variant),
        "final_prediction": redact(final_prediction),
        "forecast_source": forecast_source,
        **metadata_paths(work_dir),
        "duration_seconds": round(time.time() - started, 3),
        "completed_at": iso_now(),
    }
    write_prediction_log(entry)
    write_prediction_audit(entry)
    return final_prediction


def capture_kalshi_event_snapshot(*, work_dir: Path, request_id: str, event: dict[str, Any]) -> None:
    """Best-effort frozen Kalshi event snapshot for KX events.

    This gives the live forecaster a direct exact-market baseline and preserves
    request-time market state for later scoring/debugging. It must never block a
    valid forecast if Kalshi is unavailable or the event is not a Kalshi event.
    """
    ticker = kalshi_event_ticker(event)
    if ticker is None:
        return

    try:
        snapshot = fetch_kalshi_event_snapshot(ticker)
    except Exception as exc:
        write_trace(
            work_dir=work_dir,
            request_id=request_id,
            stage="kalshi_event_snapshot_failed",
            payload={
                "ticker": ticker,
                "error": str(exc),
                "notes": "Forecast may continue; exact Kalshi request-time snapshot was unavailable.",
            },
            mirror_dir=TRACE_LOG_DIR,
        )
        append_evidence_item(
            work_dir=work_dir,
            item={
                "kind": "prediction_market_snapshot",
                "source": "kalshi_event_api",
                "ticker": ticker,
                "usable": False,
                "notes": f"Direct Kalshi event snapshot failed for {ticker}: {exc}",
            },
        )
        return

    snapshot_path = work_dir / KALSHI_EVENT_SNAPSHOT_NAME
    snapshot_path.write_text(json.dumps(redact(snapshot), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    summary = summarize_kalshi_event_snapshot(snapshot)
    append_evidence_item(
        work_dir=work_dir,
        item={
            "kind": "prediction_market_snapshot",
            "source": "kalshi_event_api",
            "ticker": ticker,
            "snapshot_path": str(snapshot_path),
            "summary": summary,
            "notes": (
                "Frozen request-time Kalshi event snapshot. Use this exact-market baseline "
                "before generic market search; preserve the snapshot for later Brier analysis."
            ),
        },
    )
    write_trace(
        work_dir=work_dir,
        request_id=request_id,
        stage="kalshi_event_snapshot_captured",
        payload={
            "ticker": ticker,
            "snapshot_path": str(snapshot_path),
            "summary": summary,
        },
        mirror_dir=TRACE_LOG_DIR,
    )


def kalshi_event_ticker(event: dict[str, Any]) -> str | None:
    event_ticker = event.get("event_ticker")
    if isinstance(event_ticker, str):
        ticker = event_ticker.strip().upper()
        if ticker.startswith("KX"):
            return ticker

    market_ticker = event.get("market_ticker")
    if isinstance(market_ticker, str):
        ticker = market_ticker.strip().upper()
        if not ticker.startswith("KX"):
            return None
        parts = ticker.split("-")
        if len(parts) >= 3:
            return "-".join(parts[:2])
        return ticker
    return None


def fetch_kalshi_event_snapshot(ticker: str) -> dict[str, Any]:
    encoded = urllib.parse.quote(ticker, safe="")
    url = f"{KALSHI_EVENT_API_BASE}/events/{encoded}"
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "ClaudeProphet/1.0"})
    with urllib.request.urlopen(request, timeout=5.0) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Kalshi event response for {ticker} was not a JSON object.")
    return payload


def summarize_kalshi_event_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    event = snapshot.get("event") if isinstance(snapshot.get("event"), dict) else {}
    markets = snapshot.get("markets") if isinstance(snapshot.get("markets"), list) else []
    rows = []
    for market in markets:
        if not isinstance(market, dict):
            continue
        rows.append(
            {
                "ticker": market.get("ticker"),
                "label": kalshi_market_label(market),
                "yes_bid": dollars_probability(market.get("yes_bid_dollars")),
                "yes_ask": dollars_probability(market.get("yes_ask_dollars")),
                "midpoint": market_midpoint(market),
                "last_price": dollars_probability(market.get("last_price_dollars")),
                "previous_price": dollars_probability(market.get("previous_price_dollars")),
                "status": market.get("status"),
                "result": market.get("result"),
                "volume": numeric_value(market.get("volume_fp")),
                "open_interest": numeric_value(market.get("open_interest_fp")),
            }
        )
    return {
        "event_ticker": event.get("event_ticker"),
        "title": event.get("title"),
        "mutually_exclusive": event.get("mutually_exclusive"),
        "category": event.get("category"),
        "market_count": len(rows),
        "markets": rows,
    }


def kalshi_market_label(market: dict[str, Any]) -> str | None:
    for key in ("yes_sub_title", "no_sub_title"):
        value = market.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    custom = market.get("custom_strike")
    if isinstance(custom, dict):
        for value in custom.values():
            if value is not None:
                return str(value).strip()
    return None


def kalshi_snapshot_prediction(
    work_dir: Path,
    event: dict[str, Any],
    *,
    require_resolved: bool,
) -> dict[str, Any] | None:
    snapshot_path = work_dir / KALSHI_EVENT_SNAPSHOT_NAME
    if not snapshot_path.exists():
        return None
    try:
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    outcomes = event_outcomes(event)
    if not outcomes:
        outcomes = ["Yes", "No"]
    probabilities = kalshi_snapshot_probabilities(
        snapshot,
        event,
        outcomes,
        require_resolved=require_resolved,
    )
    if probabilities is None:
        return None

    forecast_source = "kalshi_resolved_snapshot" if require_resolved else "kalshi_snapshot_fallback"
    rationale = (
        "Kalshi snapshot captured at request time already contained finalized outcomes, so Claude was bypassed."
        if require_resolved
        else "Fallback forecast returned from the request-time Kalshi snapshot because Claude did not finish."
    )
    return {
        "probabilities": [
            {"market": outcome, "probability": round(probabilities[outcome], 6)}
            for outcome in outcomes
        ],
        "rationale": rationale,
        "_forecast_source": forecast_source,
    }


def kalshi_snapshot_probabilities(
    snapshot: dict[str, Any],
    event: dict[str, Any],
    outcomes: list[str],
    *,
    require_resolved: bool,
) -> dict[str, float] | None:
    markets = [
        item
        for item in snapshot.get("markets", [])
        if isinstance(item, dict)
    ] if isinstance(snapshot.get("markets"), list) else []
    if not markets:
        return None

    if is_binary_yes_no_outcomes(outcomes):
        market = select_kalshi_binary_market(markets, event)
        if market is None:
            return None
        yes_probability = kalshi_market_probability(market, require_resolved=require_resolved)
        if yes_probability is None:
            return None
        return {
            outcome: yes_probability if canonical_market_label(outcome) == "yes" else 1.0 - yes_probability
            for outcome in outcomes
        }

    by_label: dict[str, dict[str, Any]] = {}
    for market in markets:
        label = kalshi_market_label(market)
        if label:
            by_label[canonical_market_label(label)] = market

    probabilities: dict[str, float] = {}
    for outcome in outcomes:
        market = by_label.get(canonical_market_label(outcome))
        if market is None:
            return None
        probability = kalshi_market_probability(market, require_resolved=require_resolved)
        if probability is None:
            return None
        probabilities[outcome] = probability
    return probabilities


def select_kalshi_binary_market(markets: list[dict[str, Any]], event: dict[str, Any]) -> dict[str, Any] | None:
    market_ticker = event.get("market_ticker")
    if isinstance(market_ticker, str):
        normalized_ticker = market_ticker.strip().upper()
        for market in markets:
            ticker = market.get("ticker")
            if isinstance(ticker, str) and ticker.strip().upper() == normalized_ticker:
                return market
    if len(markets) == 1:
        return markets[0]
    return None


def is_binary_yes_no_outcomes(outcomes: list[str]) -> bool:
    return {canonical_market_label(outcome) for outcome in outcomes} == {"yes", "no"}


def kalshi_market_probability(market: dict[str, Any], *, require_resolved: bool) -> float | None:
    result = str(market.get("result") or "").strip().lower()
    if result in {"yes", "y"}:
        return 1.0
    if result in {"no", "n"}:
        return 0.0

    for key in ("settlement_value_dollars", "settlement_value"):
        probability = dollars_probability(market.get(key))
        if probability is not None:
            return probability

    if require_resolved:
        return None
    return market_midpoint(market)


def canonical_market_label(label: str) -> str:
    normalized = label.lower().strip()
    normalized = normalized.replace("$", "")
    normalized = normalized.replace(",", "")
    normalized = re.sub(r"\s+", " ", normalized)
    normalized = re.sub(r"\d+(?:\.\d+)?", normalize_label_number, normalized)
    return normalized


def normalize_label_number(match: re.Match[str]) -> str:
    raw = match.group(0)
    if "." not in raw:
        return str(int(raw))
    return f"{float(raw):g}"


def market_midpoint(market: dict[str, Any]) -> float | None:
    bid = dollars_probability(market.get("yes_bid_dollars"))
    ask = dollars_probability(market.get("yes_ask_dollars"))
    if bid is not None and ask is not None:
        return round((bid + ask) / 2, 6)
    return dollars_probability(market.get("last_price_dollars"))


def dollars_probability(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed > 1:
        parsed /= 100.0
    if parsed < 0 or parsed > 1:
        return None
    return round(parsed, 6)


def numeric_value(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@app.get("/prophet/events")
async def prophet_events(
    status: str = Query(default="all", pattern="^(all|open|closed)$"),
    server_url: str | None = None,
    api_key: str | None = None,
) -> list[dict[str, Any]]:
    client = make_client(server_url, api_key)
    try:
        events = client.get_forecast_events(status=status)
        return [item.model_dump(mode="json") for item in events]
    finally:
        client.close()


@app.post("/prophet/register-team")
async def prophet_register_team(payload: RegisterRequest) -> dict[str, Any]:
    client = make_client(payload.server_url, payload.api_key)
    try:
        result = client.register_forecast_team(
            team_name=payload.team_name,
            endpoint_url=payload.endpoint_url,
            is_active=payload.is_active,
        )
        return result.model_dump(mode="json")
    finally:
        client.close()


@app.post("/prophet/register-endpoint")
async def prophet_register_endpoint(payload: EndpointRegisterRequest) -> dict[str, Any]:
    client = make_client(payload.server_url, payload.api_key)
    try:
        result = client.register_forecast_endpoint(
            team_name=payload.team_name,
            endpoint_url=payload.endpoint_url,
            is_active=payload.is_active,
        )
        return result.model_dump(mode="json")
    finally:
        client.close()


@app.get("/prophet/endpoint/{team_name}")
async def prophet_endpoint(team_name: str, server_url: str | None = None, api_key: str | None = None) -> dict[str, Any]:
    client = make_client(server_url, api_key)
    try:
        result = client.get_forecast_endpoint(team_name)
        if result is None:
            raise HTTPException(status_code=404, detail="No endpoint registered for team.")
        return result.model_dump(mode="json")
    finally:
        client.close()


@app.get("/prophet/leaderboard")
async def prophet_leaderboard(server_url: str | None = None, api_key: str | None = None) -> list[dict[str, Any]]:
    client = make_client(server_url, api_key)
    try:
        scores = client.get_forecast_leaderboard()
        return [item.model_dump(mode="json") for item in scores]
    finally:
        client.close()


def run_claude_forecast(
    event_path: Path,
    *,
    request_id: str | None = None,
    variant: dict[str, Any] | None = None,
) -> dict[str, Any]:
    output_path = event_path.parent / "claude_final.json"
    command = [
        str(ROOT / "scripts" / "run_goal_exec.sh"),
        str(event_path),
    ]
    env = os.environ.copy()
    env.setdefault("CLAUDE_FORECAST_MODEL", DEFAULT_MODEL)
    env.setdefault("CLAUDE_FORECAST_SERVICE_TIER", DEFAULT_CLAUDE_SERVICE_TIER)
    timeout_seconds = claude_timeout_seconds()
    evaluation_seconds = evaluation_timeout_seconds()
    env.setdefault(
        "CLAUDE_FORECAST_TIME_BUDGET_SECONDS",
        str(int(forecast_time_budget_seconds(timeout_seconds, evaluation_seconds))),
    )
    env.setdefault(
        "CLAUDE_FORECAST_EVALUATION_TIMEOUT_SECONDS",
        str(int(evaluation_seconds)),
    )
    if variant:
        env.setdefault("CLAUDE_FORECAST_VARIANT_ID", str(variant.get("variant_id") or "unknown"))
        env.setdefault("CLAUDE_FORECAST_VARIANT_JSON", json.dumps(redact(variant), sort_keys=True))
    env.setdefault("CLAUDE_REQUEST_WORKSPACE", str(event_path.parent))
    env.setdefault("CLAUDE_EVIDENCE_MANIFEST", str(event_path.parent / "evidence_manifest.json"))
    env.setdefault("CLAUDE_TRACE_LOG", str(event_path.parent / "trace.jsonl"))
    trace_request_id = request_id or event_path.parent.name
    write_trace(
        work_dir=event_path.parent,
        request_id=trace_request_id,
        stage="claude_started",
        payload={
            "command": " ".join(command),
            "model": env.get("CLAUDE_FORECAST_MODEL"),
            "service_tier": env.get("CLAUDE_FORECAST_SERVICE_TIER"),
            "timeout_seconds": timeout_seconds,
            "variant": redact(variant or {}),
            **metadata_paths(event_path.parent),
        },
        mirror_dir=TRACE_LOG_DIR,
    )
    event = json.loads(event_path.read_text(encoding="utf-8"))
    process = subprocess.Popen(
        command,
        cwd=ROOT,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    write_trace(
        work_dir=event_path.parent,
        request_id=trace_request_id,
        stage="claude_process_started",
        payload={"pid": process.pid},
        mirror_dir=TRACE_LOG_DIR,
    )
    deadline = time.monotonic() + timeout_seconds
    completed: subprocess.CompletedProcess[str] | None = None
    while True:
        returncode = process.poll()
        if returncode is not None:
            stdout, stderr = process.communicate()
            completed = subprocess.CompletedProcess(command, returncode, stdout, stderr)
            break

        final_checkpoint = checkpoint_prediction(event_path.parent, event, "final")
        if final_checkpoint:
            output_prediction = dict(final_checkpoint)
            output_prediction.pop("_checkpoint_kind", None)
            output_prediction["_forecast_source"] = "final_checkpoint"
            stdout, stderr = wait_after_final_checkpoint(
                process,
                deadline=deadline,
                grace_seconds=final_checkpoint_grace_seconds(),
            )
            (event_path.parent / "claude_stdout.txt").write_text(stdout or "", encoding="utf-8")
            (event_path.parent / "claude_stderr.txt").write_text(stderr or "", encoding="utf-8")
            persisted_prediction = dict(output_prediction)
            persisted_prediction.pop("_forecast_source", None)
            output_path.write_text(json.dumps(persisted_prediction, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            write_trace(
                work_dir=event_path.parent,
                request_id=trace_request_id,
                stage="claude_final_checkpoint_detected",
                payload={
                    "output_path": str(output_path),
                    "post_final_grace_seconds": final_checkpoint_grace_seconds(),
                },
                mirror_dir=TRACE_LOG_DIR,
            )
            return output_prediction

        if time.monotonic() >= deadline:
            stdout, stderr = stop_process_tree(process, graceful_timeout=0)
            (event_path.parent / "claude_stdout.txt").write_text(stdout or "", encoding="utf-8")
            (event_path.parent / "claude_stderr.txt").write_text(stderr or "", encoding="utf-8")
            write_trace(
                work_dir=event_path.parent,
                request_id=trace_request_id,
                stage="claude_timeout",
                payload={"timeout_seconds": timeout_seconds},
                mirror_dir=TRACE_LOG_DIR,
            )
            checkpoint = latest_checkpoint_prediction(event_path.parent, event)
            if checkpoint:
                source_kind = checkpoint.get("_checkpoint_kind", "checkpoint")
                output_prediction = dict(checkpoint)
                output_prediction.pop("_checkpoint_kind", None)
                output_prediction["_forecast_source"] = source_kind
                persisted_prediction = dict(output_prediction)
                persisted_prediction.pop("_forecast_source", None)
                output_path.write_text(json.dumps(persisted_prediction, indent=2, sort_keys=True) + "\n", encoding="utf-8")
                write_trace(
                    work_dir=event_path.parent,
                    request_id=trace_request_id,
                    stage="claude_timeout_checkpoint_returned",
                    payload={"forecast_source": source_kind, "output_path": str(output_path)},
                    mirror_dir=TRACE_LOG_DIR,
                )
                return output_prediction
            raise TimeoutError(f"Claude exceeded internal timeout of {timeout_seconds:.0f}s")

        time.sleep(1.0)

    assert completed is not None
    (event_path.parent / "claude_stdout.txt").write_text(completed.stdout, encoding="utf-8")
    (event_path.parent / "claude_stderr.txt").write_text(completed.stderr, encoding="utf-8")
    write_trace(
        work_dir=event_path.parent,
        request_id=trace_request_id,
        stage="claude_finished",
        payload={"returncode": completed.returncode},
        mirror_dir=TRACE_LOG_DIR,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout)[-2000:]
        write_trace(
            work_dir=event_path.parent,
            request_id=trace_request_id,
            stage="claude_failed",
            payload={"returncode": completed.returncode, "error_tail": detail},
            mirror_dir=TRACE_LOG_DIR,
        )
        raise RuntimeError(detail or f"Claude exited with code {completed.returncode}")

    prediction = checkpoint_or_stdout_prediction(event_path.parent, completed.stdout, event_path)
    output_path.write_text(json.dumps(prediction, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_trace(
        work_dir=event_path.parent,
        request_id=trace_request_id,
        stage="claude_prediction_parsed",
        payload={"output_path": str(output_path)},
        mirror_dir=TRACE_LOG_DIR,
    )
    return prediction


def wake_openclaw_observer_once(
    *,
    work_dir: Path,
    request_id: str,
    event: dict[str, Any],
    variant: dict[str, Any],
) -> None:
    """Best-effort, non-blocking observer wake for one forecast request."""
    if not openclaw_observer_enabled():
        return

    marker_path = work_dir / "openclaw_observer.json"
    if marker_path.exists():
        return

    command = openclaw_observer_command(work_dir=work_dir, request_id=request_id, event=event)
    marker = {
        "request_id": request_id,
        "created_at": iso_now(),
        "event_title": event.get("title") or event.get("event_ticker") or event.get("market_ticker"),
        "observer_command": redact_command(command),
    }
    marker_path.write_text(json.dumps(marker, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    try:
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as exc:
        write_trace(
            work_dir=work_dir,
            request_id=request_id,
            stage="openclaw_observer_start_failed",
            payload={"error": str(exc), "marker_path": str(marker_path)},
            mirror_dir=TRACE_LOG_DIR,
        )
        return

    write_trace(
        work_dir=work_dir,
        request_id=request_id,
        stage="openclaw_observer_started",
        payload={
            "pid": process.pid,
            "marker_path": str(marker_path),
            "delivery_channel": os.getenv("CLAUDE_OPENCLAW_OBSERVER_CHANNEL", "telegram"),
            "delivery_to_configured": bool(os.getenv("CLAUDE_OPENCLAW_OBSERVER_TO")),
        },
        mirror_dir=TRACE_LOG_DIR,
    )


def openclaw_observer_enabled() -> bool:
    return os.getenv("CLAUDE_OPENCLAW_OBSERVER_ENABLED", "").lower() in {"1", "true", "yes", "on"}


def degraded_forecast_source(forecast_source: str) -> bool:
    return forecast_source not in {"claude_stdout", "final_checkpoint", "kalshi_resolved_snapshot"}


def openclaw_observer_command(*, work_dir: Path, request_id: str, event: dict[str, Any]) -> list[str]:
    binary = os.getenv("CLAUDE_OPENCLAW_BIN") or DEFAULT_OPENCLAW_BIN
    timeout_seconds = os.getenv("CLAUDE_OPENCLAW_OBSERVER_TIMEOUT_SECONDS", str(DEFAULT_OPENCLAW_OBSERVER_TIMEOUT_SECONDS))
    delivery_channel = os.getenv("CLAUDE_OPENCLAW_OBSERVER_CHANNEL", "telegram")
    delivery_to = os.getenv("CLAUDE_OPENCLAW_OBSERVER_TO")
    success_email_to = os.getenv("CLAUDE_OPENCLAW_OBSERVER_SUCCESS_EMAIL_TO")
    success_email_account = os.getenv("CLAUDE_OPENCLAW_OBSERVER_SUCCESS_EMAIL_ACCOUNT", "wenhanson0@gmail.com")
    failure_email_to = os.getenv("CLAUDE_OPENCLAW_OBSERVER_FAILURE_EMAIL_TO")
    failure_email_account = os.getenv("CLAUDE_OPENCLAW_OBSERVER_FAILURE_EMAIL_ACCOUNT", "wenhanson0@gmail.com")
    title = event.get("title") or event.get("event_ticker") or event.get("market_ticker") or "Untitled event"
    command = [
        sys.executable,
        str(ROOT / "scripts" / "openclaw_observer.py"),
        "--workspace",
        str(work_dir),
        "--request-id",
        request_id,
        "--event-title",
        str(title),
        "--max-wait-seconds",
        timeout_seconds,
        "--openclaw-bin",
        binary,
        "--delivery-channel",
        delivery_channel,
    ]
    ticker = event.get("event_ticker") or event.get("market_ticker")
    if ticker:
        command.extend(["--event-ticker", str(ticker)])
    if delivery_to:
        command.extend(["--delivery-to", delivery_to])
    if success_email_to:
        command.extend(["--success-email-to", success_email_to, "--success-email-account", success_email_account])
    if failure_email_to:
        command.extend(["--failure-email-to", failure_email_to, "--failure-email-account", failure_email_account])
    return command


def redact_command(command: list[str]) -> list[str]:
    redacted: list[str] = []
    skip_next = False
    for item in command:
        if skip_next:
            redacted.append("[redacted]")
            skip_next = False
            continue
        redacted.append(item)
        if item in {"--token"}:
            skip_next = True
    return redacted


def wait_after_final_checkpoint(
    process: subprocess.Popen[str],
    *,
    deadline: float,
    grace_seconds: float,
) -> tuple[str, str]:
    """Let Claude finish post-final cleanup briefly, then stop the process group."""
    grace_deadline = min(deadline, time.monotonic() + max(0.0, grace_seconds))
    while time.monotonic() < grace_deadline:
        if process.poll() is not None:
            return process.communicate()
        time.sleep(1.0)
    return stop_process_tree(process, graceful_timeout=5)


def report_forecast_issue(
    *,
    work_dir: Path,
    event_path: Path,
    error: BaseException,
    forecast_source: str,
) -> None:
    """Best-effort GitHub issue creation for production forecast failures."""
    script = ROOT / "scripts" / "report_tool_failure_issue.sh"
    if not script.exists():
        return

    error_file = work_dir / "claude_stderr.txt"
    if not error_file.exists():
        error_file.write_text(str(error), encoding="utf-8")

    command_text = f"{ROOT / 'scripts' / 'run_goal_exec.sh'} {event_path}"
    notes = f"API returned {forecast_source}; Claude forecast failed with: {str(error)[:300]}"
    args = [
        str(script),
        "--tool",
        "claude:prophet",
        "--event",
        str(event_path),
        "--command",
        command_text,
        "--error-file",
        str(error_file),
        "--notes",
        notes,
    ]
    try:
        process = subprocess.Popen(
            args,
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as issue_error:
        write_trace(
            work_dir=work_dir,
            request_id=work_dir.name,
            stage="issue_report_start_failed",
            payload={"error": str(issue_error)},
            mirror_dir=TRACE_LOG_DIR,
        )
        return

    write_trace(
        work_dir=work_dir,
        request_id=work_dir.name,
        stage="issue_report_started",
        payload={"pid": process.pid, "forecast_source": forecast_source},
        mirror_dir=TRACE_LOG_DIR,
    )


def stop_process_tree(
    process: subprocess.Popen[str],
    *,
    graceful_timeout: float,
) -> tuple[str, str]:
    """Stop the shell and its Claude child process group without hanging pipes."""
    if process.poll() is not None:
        return process.communicate()

    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except Exception:
        process.terminate()

    if graceful_timeout > 0:
        try:
            return process.communicate(timeout=graceful_timeout)
        except subprocess.TimeoutExpired:
            pass

    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except Exception:
        process.kill()

    try:
        return process.communicate(timeout=2)
    except subprocess.TimeoutExpired:
        return "", "Claude process group did not release pipes after SIGKILL."


def claude_timeout_seconds() -> float:
    default_timeout = DEFAULT_PUBLIC_CLAUDE_TIMEOUT_SECONDS if PUBLIC_API_MODE else DEFAULT_CLAUDE_TIMEOUT_SECONDS
    return float(os.getenv("CLAUDE_API_TIMEOUT", str(int(default_timeout))))


def final_checkpoint_grace_seconds() -> float:
    return float(os.getenv("CLAUDE_FINAL_CHECKPOINT_GRACE_SECONDS", str(int(DEFAULT_FINAL_CHECKPOINT_GRACE_SECONDS))))


def evaluation_timeout_seconds() -> float:
    return float(
        os.getenv(
            "CLAUDE_EVALUATION_TIMEOUT",
            str(int(DEFAULT_EVALUATION_TIMEOUT_SECONDS)),
        )
    )


def forecast_return_buffer_seconds() -> float:
    return float(
        os.getenv(
            "CLAUDE_FORECAST_RETURN_BUFFER_SECONDS",
            str(int(DEFAULT_FORECAST_RETURN_BUFFER_SECONDS)),
        )
    )


def forecast_time_budget_seconds(timeout_seconds: float, evaluation_seconds: float) -> float:
    """Tell the agent to finish before the API kill switch.

    `CLAUDE_API_TIMEOUT` is a process lifecycle limit, not usable research time.
    If we give the agent the full kill timeout as its internal budget, short
    public requests cluster at the timeout and return degraded checkpoints.
    """

    configured_budget = os.getenv("CLAUDE_FORECAST_TIME_BUDGET_SECONDS")
    if configured_budget:
        return float(configured_budget)
    hard_limit = min(timeout_seconds, evaluation_seconds)
    buffer_seconds = min(forecast_return_buffer_seconds(), max(0.0, hard_limit / 3.0))
    return max(10.0, hard_limit - buffer_seconds)


def checkpoint_or_stdout_prediction(work_dir: Path, stdout: str, event_path: Path) -> dict[str, Any]:
    event = json.loads(event_path.read_text(encoding="utf-8"))
    stdout_prediction: dict[str, Any] | None = None
    try:
        stdout_prediction = parse_prediction_json(stdout)
        validation = validate_submission(stdout_prediction, event=event, require_probability_sum=False)
        if validation.valid:
            return validation.normalized_prediction or stdout_prediction
    except Exception:
        stdout_prediction = None

    checkpoint = latest_checkpoint_prediction(work_dir, event)
    if checkpoint:
        checkpoint.pop("_checkpoint_kind", None)
        return checkpoint
    if stdout_prediction is not None:
        return stdout_prediction
    raise ValueError("Could not parse a JSON prediction from Claude output or checkpoints.")


def latest_checkpoint_prediction(work_dir: Path, event: dict[str, Any]) -> dict[str, Any] | None:
    for kind in ("final", "initial"):
        prediction = checkpoint_prediction(work_dir, event, kind)
        if prediction:
            return prediction
    return None


def checkpoint_prediction(work_dir: Path, event: dict[str, Any], kind: str) -> dict[str, Any] | None:
    path = work_dir / f"{kind}_submission.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    prediction = payload.get("prediction") if isinstance(payload, dict) else None
    validation = validate_submission(prediction, event=event, require_probability_sum=False)
    if validation.valid:
        output = dict(validation.normalized_prediction or prediction)
        output["_checkpoint_kind"] = f"{kind}_checkpoint"
        return output
    return None


def fallback_prediction(event: dict[str, Any], *, reason: str) -> dict[str, Any]:
    outcomes = event_outcomes(event)
    if not outcomes:
        outcomes = ["Yes", "No"]

    probabilities = market_data_probabilities(event, outcomes)
    if probabilities is None:
        probabilities = default_probabilities(event, outcomes)

    return {
        "probabilities": [
            {"market": outcome, "probability": round(clamp_probability(probabilities[outcome]), 6)}
            for outcome in outcomes
        ],
        "rationale": (
            "Fallback forecast returned because Claude did not finish inside the internal time budget. "
            f"Used event-provided market data when available, otherwise a conservative prior. Reason: {reason[:180]}"
        ),
    }


def event_outcomes(event: dict[str, Any]) -> list[str]:
    raw = event.get("outcomes") or event.get("markets")
    if isinstance(raw, list):
        return [item.strip() for item in raw if isinstance(item, str) and item.strip()]
    return []


def market_data_probabilities(event: dict[str, Any], outcomes: list[str]) -> dict[str, float] | None:
    raw = event.get("market_data") or event.get("market_info")
    if not isinstance(raw, dict):
        return None

    values: dict[str, float] = {}
    for outcome in outcomes:
        item = raw.get(outcome)
        if not isinstance(item, dict):
            continue
        probability = probability_from_market_record(item)
        if probability is not None:
            values[outcome] = probability

    if len(values) == len(outcomes):
        return values
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


def default_probabilities(event: dict[str, Any], outcomes: list[str]) -> dict[str, float]:
    text = " ".join(
        str(event.get(key) or "") for key in ("title", "description", "rules")
    ).lower()
    if len(outcomes) == 2:
        return {outcome: 0.5 for outcome in outcomes}
    if any(term in text for term in ("top 5", "top five", "top 3", "top three", "podium")):
        k = 5 if "top 5" in text or "top five" in text else 3
        p = min(0.99, max(0.01, k / len(outcomes)))
        return {outcome: p for outcome in outcomes}
    p = 1.0 / len(outcomes)
    return {outcome: p for outcome in outcomes}


def normalize_probability(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not number == number:
        return None
    if number > 1.0:
        number /= 100.0
    return clamp_probability(number)


def clamp_probability(value: float) -> float:
    return min(0.99, max(0.01, value))


def parse_prediction_json(text: str) -> dict[str, Any]:
    stripped = text.strip()
    candidates = [stripped]
    first = stripped.find("{")
    last = stripped.rfind("}")
    if first != -1 and last != -1 and last > first:
        candidates.append(stripped[first : last + 1])
    candidates.extend(extract_json_objects(stripped))
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            if "prediction" in parsed and isinstance(parsed["prediction"], dict):
                return parsed["prediction"]
            return parsed
    raise ValueError("Could not parse a JSON prediction from Claude output.")


def extract_json_objects(text: str) -> list[str]:
    objects: list[str] = []
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            _, end = decoder.raw_decode(text[match.start() :])
        except json.JSONDecodeError:
            continue
        objects.append(text[match.start() : match.start() + end])
    return objects


def make_client(server_url: str | None, api_key: str | None):
    from ai_prophet_core import DEFAULT_API_URL
    from ai_prophet_core.client import ServerAPIClient

    return ServerAPIClient(
        base_url=server_url or os.getenv("PA_SERVER_URL", DEFAULT_API_URL),
        api_key=resolve_api_key(api_key, required=True),
    )


def resolve_api_key(api_key: str | None, *, required: bool) -> str | None:
    value = api_key or os.getenv("PA_SERVER_API_KEY") or os.getenv("PROPHETHACKS_SERVER_API_KEY")
    if required and not value:
        raise HTTPException(
            status_code=500,
            detail="Prophet API key missing. Set PA_SERVER_API_KEY or PROPHETHACKS_SERVER_API_KEY in .env.",
        )
    return value


def write_prediction_log(entry: dict[str, Any]) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with PREDICTION_LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")


def write_prediction_audit(entry: dict[str, Any]) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    safe_entry = redact(entry)
    with AUDIT_JSONL.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(safe_entry, sort_keys=True) + "\n")
    with AUDIT_MARKDOWN.open("a", encoding="utf-8") as handle:
        handle.write(render_audit_markdown(safe_entry))


def render_audit_markdown(entry: dict[str, Any]) -> str:
    event = entry.get("event") if isinstance(entry.get("event"), dict) else {}
    title = event.get("title") or event.get("event_ticker") or "Untitled event"
    timestamp = entry.get("completed_at") or entry.get("received_at") or iso_now()
    parts = [
        f"\n## {timestamp} - {entry.get('stage', 'unknown')}\n\n",
        f"- Request ID: `{entry.get('request_id', 'unknown')}`\n",
        f"- Event: {title}\n",
    ]
    if "duration_seconds" in entry:
        parts.append(f"- Duration: `{entry['duration_seconds']}` seconds\n")
    parts.append("\n### Input Event\n\n")
    parts.append("```json\n")
    parts.append(json.dumps(event, indent=2, sort_keys=True))
    parts.append("\n```\n")
    prediction = entry.get("final_prediction") or entry.get("prediction")
    if prediction is not None:
        parts.append("\n### Prediction Output\n\n")
        parts.append("```json\n")
        parts.append(json.dumps(prediction, indent=2, sort_keys=True))
        parts.append("\n```\n")
    if "validation" in entry:
        parts.append("\n### Validation\n\n")
        parts.append("```json\n")
        parts.append(json.dumps(entry["validation"], indent=2, sort_keys=True))
        parts.append("\n```\n")
    if "error" in entry:
        parts.append("\n### Error\n\n")
        parts.append("```text\n")
        parts.append(str(entry["error"]))
        parts.append("\n```\n")
    return "".join(parts)


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            lower = str(key).lower()
            if "key" in lower or "token" in lower or "secret" in lower or "authorization" in lower:
                redacted[key] = "[redacted]"
            else:
                redacted[key] = redact(item)
        return redacted
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> None:
    parser = argparse.ArgumentParser(prog="api_service")
    parser.add_argument("--host", default=os.getenv("CLAUDE_API_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("CLAUDE_API_PORT", "8080")))
    args = parser.parse_args()
    uvicorn.run("api_service.app:app", host=args.host, port=args.port, reload=False)

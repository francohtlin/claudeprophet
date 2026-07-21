"""Orchestrate a backtest: cases -> forecasts -> scored records + per-forecaster metrics."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from backtest.data import Case
from backtest.forecasters.base import Forecaster
from backtest.scoring import summarize


def run_backtest(cases: list[Case], forecasters: list[Forecaster]) -> dict[str, Any]:
    # Each forecaster produces one prediction per case (walk-forward internally).
    predictions: dict[str, list[float | None]] = {
        f.name: f.predict_all(cases) for f in forecasters
    }

    records: list[dict[str, Any]] = []
    for i, case in enumerate(cases):
        record = {
            "ticker": case.ticker,
            "series_ticker": case.series_ticker,
            "title": case.title,
            "close_time": case.close_time,
            "outcome": case.outcome,
            "forecasts": {name: predictions[name][i] for name in predictions},
        }
        records.append(record)

    metrics: dict[str, Any] = {}
    for f in forecasters:
        pairs = [
            (predictions[f.name][i], cases[i].outcome)
            for i in range(len(cases))
            if predictions[f.name][i] is not None
        ]
        metrics[f.name] = {
            "leakage_safe": f.leakage_safe,
            "coverage": len(pairs),
            **summarize(pairs),
        }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n_cases": len(cases),
        "forecasters": [f.name for f in forecasters],
        "metrics": metrics,
        "records": records,
    }

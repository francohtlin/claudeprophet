"""Polymarket track configs. Two parallel tracks share the same pipeline
(pull/select/forecast/portfolio) but use separate files and a different source
filter, so 'earnings' (beat/miss) and 'kpis' (revenue/deliveries/growth ladders)
never mix.

Every PM pipeline script takes `--track earnings|kpis` and resolves its paths
from here. Default is 'earnings' so pre-existing behaviour is unchanged.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
FC = DATA / "forecasts"

TRACKS = {
    # legacy file names (…_kpi_… / …_pm…) kept for the earnings track to avoid
    # churn; the KPI track uses explicit …kpi… names.
    "earnings": {
        "label": "earnings",
        "open": DATA / "polymarket_kpi_open.jsonl",
        "chosen": FC / "_chosen_pm.json",
        "fcst": FC / "open_pm_claudeprophet.jsonl",
        "ledger": DATA / "pm_portfolio.json",
        "source": {"earnings_only": True},
    },
    "kpis": {
        "label": "KPIs",
        "open": DATA / "polymarket_kpis_open.jsonl",
        "chosen": FC / "_chosen_pmkpi.json",
        "fcst": FC / "open_pmkpi_claudeprophet.jsonl",
        "ledger": DATA / "pmkpi_portfolio.json",
        "source": {"kpis_only": True},
    },
}


def cfg(track: str) -> dict:
    if track not in TRACKS:
        raise SystemExit(f"unknown track {track!r}; choose from {list(TRACKS)}")
    return TRACKS[track]

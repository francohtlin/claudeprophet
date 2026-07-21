"""Shared logic for turning raw Kalshi company-KPI contracts into forecastable
metrics (threshold ladders collapsed). Used by the selector, the forecaster, and
the dashboard generator so the parsing lives in exactly one place.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
OPEN_MARKETS = DATA_DIR / "company_kpi_open.jsonl"
FORECASTS = DATA_DIR / "forecasts" / "open_kpi_claudeprophet.jsonl"

SCALE = {"trillion": 1e12, "billion": 1e9, "million": 1e6, "thousand": 1e3}
_PAT = re.compile(
    r"(?:above|below|at least|over|under)\s+\$?([\d,\.]+)\s*"
    r"(trillion|billion|million|thousand)?\s+(.*?)\s+"
    r"(?:in|for|during)\s+(Q[1-4]\s*\d{4}|H[12]\s*\d{4}|FY\s*\d{4}|\d{4})",
    re.I,
)


def parse(question: str) -> tuple[float, str, str] | None:
    """Return (threshold_value, metric_label, period) or None if unparseable."""
    m = _PAT.search(question or "")
    if not m:
        return None
    val = float(m.group(1).replace(",", "")) * SCALE.get((m.group(2) or "").lower(), 1.0)
    metric = re.sub(r"\s+", " ", m.group(3).strip()).title()
    period = re.sub(r"\s+", " ", m.group(4).strip().upper())
    return val, metric, period


def fmtv(x: float) -> str:
    if x >= 1e9:
        return f"{x/1e9:.2f}B"
    if x >= 1e6:
        return f"{x/1e6:.2f}M" if x < 1e7 else f"{x/1e6:.1f}M"
    if x >= 1e3:
        return f"{x/1e3:.0f}K"
    return f"{x:g}"


def implied_median(ladder: list[tuple[float, float | None]]) -> tuple[str, float] | None:
    """Market-implied central value: (op, value) where op is '~', '<', or '>'.

    Interpolates the threshold at which P(Yes) crosses 0.5 from the ladder.
    """
    lad = sorted([(v, p) for v, p in ladder if p is not None])
    if not lad:
        return None
    prev = None
    for v, p in lad:
        if prev is None:
            if p < 0.5:
                return ("<", lad[0][0])
            prev = (v, p)
            continue
        if p < 0.5 <= prev[1]:
            v0, p0 = prev
            return ("~", v0 + ((p0 - 0.5) / (p0 - p)) * (v - v0) if p0 != p else v)
        prev = (v, p)
    return (">", lad[-1][0])


def group_markets(rows: list[dict[str, Any]]) -> dict[tuple, dict[str, Any]]:
    """Group raw open-market rows into metric groups keyed by
    (company, metric, period, resolves). Each group carries its markets and,
    where parseable, the numeric threshold ladder.
    """
    groups: dict[tuple, dict[str, Any]] = defaultdict(lambda: {"markets": []})
    for r in rows:
        co = (r.get("company") or "").replace(" KPI", "").strip()
        p = parse(r.get("question", ""))
        if p:
            val, metric, period = p
            key = (co, metric, period, (r.get("close_time") or "")[:10])
        else:
            key = (co, re.sub(r"[\d,\.]+", "#", r.get("question", "")), "", (r.get("close_time") or "")[:10])
            val = None
        g = groups[key]
        g["co"], g["metric"], g["period"], g["r"] = key
        g["markets"].append((val, r.get("yes_mid"), int(r.get("volume") or 0)))
    return groups

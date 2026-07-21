"""Turn resolved Kalshi markets into scored backtest cases."""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any

from backtest.kalshi import fetch_candlesticks, list_markets


@dataclass
class Case:
    ticker: str
    series_ticker: str
    title: str
    close_time: str          # ISO 8601
    close_ts: int            # unix seconds
    result: str              # "yes" / "no"
    outcome: int             # 1 if yes else 0
    snapshot_yes_price: float | None = None   # market yes-price snapshot_days before close
    snapshot_days: int | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def event(self) -> dict[str, Any]:
        """A ClaudeProphet-style event dict (outcomes hidden from the result)."""
        return {
            "title": self.title,
            "outcomes": ["Yes", "No"],
            "close_time": self.close_time,
            "market_ticker": self.ticker,
            "event_ticker": self.meta.get("event_ticker"),
        }

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_iso_ts(value: str | None) -> int | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        return int(datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp())
    except ValueError:
        return None


def market_to_case(raw: dict[str, Any], *, snapshot_days: int | None = None) -> Case | None:
    result = str(raw.get("result") or "").lower()
    if result not in {"yes", "no"}:
        return None
    close_time = raw.get("close_time") or raw.get("expiration_time")
    close_ts = parse_iso_ts(close_time)
    if close_ts is None:
        return None
    return Case(
        ticker=str(raw.get("ticker") or ""),
        series_ticker=str(raw.get("series_ticker") or _series_from_ticker(raw.get("ticker"))),
        title=str(raw.get("title") or raw.get("yes_sub_title") or ""),
        close_time=str(close_time),
        close_ts=close_ts,
        result=result,
        outcome=1 if result == "yes" else 0,
        snapshot_days=snapshot_days,
        meta={
            "event_ticker": raw.get("event_ticker"),
            "threshold": _parse_threshold(str(raw.get("title") or "")),
        },
    )


def build_cases(
    series_tickers: list[str],
    *,
    max_per_series: int = 200,
    snapshot_days: int | None = 3,
) -> list[Case]:
    """Fetch settled markets for each series and build sorted, resolved cases."""
    cases: list[Case] = []
    for series in series_tickers:
        raw_markets = list_markets(series, status="settled", max_markets=max_per_series)
        for raw in raw_markets:
            raw.setdefault("series_ticker", series)
            case = market_to_case(raw, snapshot_days=snapshot_days)
            if case is None:
                continue
            if snapshot_days is not None:
                case.snapshot_yes_price = _snapshot_yes_price(case, snapshot_days)
            cases.append(case)
    cases.sort(key=lambda c: c.close_ts)
    return cases


def _snapshot_yes_price(case: Case, snapshot_days: int) -> float | None:
    """Yes-price from a daily candle ~snapshot_days before close (leakage-safe)."""
    target = case.close_ts - snapshot_days * 86400
    window = 5 * 86400
    candles = fetch_candlesticks(
        case.series_ticker, case.ticker, start_ts=target - window, end_ts=target
    )
    price = None
    for candle in candles:  # candles are chronological; keep the last one in-window
        p = _candle_yes_price(candle)
        if p is not None:
            price = p
    return price


def _candle_yes_price(candle: dict[str, Any]) -> float | None:
    # Kalshi candle prices are dollar strings in [0,1], e.g. {"close_dollars": "0.2500"}.
    for key in ("price", "yes_bid", "yes_ask"):
        block = candle.get(key)
        if isinstance(block, dict):
            for sub in ("close_dollars", "mean_dollars", "open_dollars"):
                value = _to_float(block.get(sub))
                if value is not None:
                    return max(0.0, min(1.0, value))
    return None


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_threshold(title: str) -> float | None:
    m = re.search(r"(above|below|at least|more than|greater than)\s+\$?([0-9][0-9,\.]*)", title, re.I)
    if not m:
        return None
    try:
        return float(m.group(2).replace(",", ""))
    except ValueError:
        return None


def _series_from_ticker(ticker: Any) -> str:
    # KXCPIYOY-26JUL-4.5 -> KXCPIYOY
    return str(ticker or "").split("-", 1)[0]

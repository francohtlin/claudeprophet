"""Minimal, dependency-free Kalshi read client (stdlib urllib only).

Only the read endpoints the backtester needs: list markets for a series (by
status) and fetch candlesticks for a market. No auth required for public data.
"""

from __future__ import annotations

import json
import ssl
import urllib.parse
import urllib.request
from typing import Any

KALSHI_BASE = "https://external-api.kalshi.com/trade-api/v2"

# Valid Kalshi market status values. "finalized"/"determined" are NOT valid and
# return HTTP 400 — resolved markets are "settled" (and sometimes "closed").
VALID_STATUSES = frozenset({"open", "closed", "settled", "unopened"})

_SSL_CONTEXT = ssl.create_default_context()


def get_json(url: str, params: dict[str, Any] | None = None, *, timeout: float = 20.0) -> dict[str, Any]:
    query = urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v is not None})
    full = f"{url}?{query}" if query else url
    request = urllib.request.Request(full, headers={"accept": "application/json", "user-agent": "claudeprophet-backtest/0.1"})
    with urllib.request.urlopen(request, timeout=timeout, context=_SSL_CONTEXT) as response:
        return json.loads(response.read().decode("utf-8"))


def list_markets(
    series_ticker: str,
    *,
    status: str = "settled",
    max_markets: int = 500,
    max_pages: int = 20,
) -> list[dict[str, Any]]:
    """List markets for a series, following pagination up to caps."""
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid status {status!r}; valid: {sorted(VALID_STATUSES)}")
    out: list[dict[str, Any]] = []
    cursor = ""
    pages = 0
    while pages < max_pages and len(out) < max_markets:
        params = {"series_ticker": series_ticker, "status": status, "limit": 1000}
        if cursor:
            params["cursor"] = cursor
        payload = get_json(f"{KALSHI_BASE}/markets", params)
        markets = payload.get("markets") or []
        out.extend(m for m in markets if isinstance(m, dict))
        cursor = str(payload.get("cursor") or "")
        pages += 1
        if not cursor:
            break
    return out[:max_markets]


def list_series(category: str) -> list[dict[str, Any]]:
    payload = get_json(f"{KALSHI_BASE}/series", {"category": category})
    return [s for s in (payload.get("series") or []) if isinstance(s, dict)]


def fetch_candlesticks(
    series_ticker: str,
    market_ticker: str,
    *,
    start_ts: int,
    end_ts: int,
    period_interval: int = 1440,
) -> list[dict[str, Any]]:
    """Daily (default) candlesticks for one market between two unix timestamps.

    Returns [] on any error so callers can degrade gracefully.
    """
    url = f"{KALSHI_BASE}/series/{series_ticker}/markets/{market_ticker}/candlesticks"
    try:
        payload = get_json(
            url,
            {"start_ts": start_ts, "end_ts": end_ts, "period_interval": period_interval},
        )
    except Exception:
        return []
    candles = payload.get("candlesticks")
    return [c for c in candles if isinstance(c, dict)] if isinstance(candles, list) else []

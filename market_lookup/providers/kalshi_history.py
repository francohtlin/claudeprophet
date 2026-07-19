from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from market_lookup.providers.common import get_json, number, probability
from market_lookup.providers.kalshi import KALSHI_BASE


HISTORICAL_BASE = f"{KALSHI_BASE}/historical"
DEFAULT_PERIOD_INTERVAL_MINUTES = 60


def fetch_kalshi_market_history(
    ticker: str,
    *,
    lookback_days: int = 7,
    trade_limit: int = 50,
    candle_limit: int = 48,
    period_interval_minutes: int = DEFAULT_PERIOD_INTERVAL_MINUTES,
) -> dict[str, Any]:
    """Fetch public Kalshi trade/candle history for one market ticker.

    Kalshi splits recent and older public data around provider-defined cutoff
    timestamps. This function calls the correct live/historical endpoints and
    returns agent-safe records without market IDs or trade IDs.
    """
    ticker = ticker.strip()
    if not ticker:
        return {"trades": [], "candlesticks": [], "errors": ["missing Kalshi ticker"]}

    now = datetime.now(timezone.utc)
    start = now - timedelta(days=max(1, int(lookback_days)))
    cutoff = fetch_historical_cutoff()
    trade_cutoff = parse_iso_datetime(cutoff.get("trades_created_ts"))
    candle_cutoff = parse_iso_datetime(cutoff.get("orders_updated_ts"))

    errors: list[str] = []
    trades = fetch_trades_for_window(
        ticker,
        start=start,
        end=now,
        cutoff=trade_cutoff,
        limit=max(1, int(trade_limit)),
        errors=errors,
    )
    candlesticks = fetch_candles_for_window(
        ticker,
        start=start,
        end=now,
        cutoff=candle_cutoff,
        limit=max(1, int(candle_limit)),
        period_interval_minutes=max(1, int(period_interval_minutes)),
        errors=errors,
    )
    history: dict[str, Any] = {
        "lookback_days": max(1, int(lookback_days)),
        "trades": trades[: max(1, int(trade_limit))],
        "candlesticks": candlesticks[-max(1, int(candle_limit)) :],
    }
    if errors:
        history["errors"] = errors
    return history


def fetch_historical_cutoff() -> dict[str, Any]:
    payload = get_json(f"{HISTORICAL_BASE}/cutoff", timeout=5.0)
    return payload if isinstance(payload, dict) else {}


def fetch_trades_for_window(
    ticker: str,
    *,
    start: datetime,
    end: datetime,
    cutoff: datetime | None,
    limit: int,
    errors: list[str],
) -> list[dict[str, Any]]:
    segments = endpoint_segments(
        live_endpoint=f"{KALSHI_BASE}/markets/trades",
        historical_endpoint=f"{HISTORICAL_BASE}/trades",
        start=start,
        end=end,
        cutoff=cutoff,
    )
    trades: list[dict[str, Any]] = []
    for endpoint, segment_start, segment_end in segments:
        params = {
            "ticker": ticker,
            "limit": limit,
            "min_ts": int(segment_start.timestamp()),
            "max_ts": int(segment_end.timestamp()),
        }
        try:
            payload = get_json(endpoint, params, timeout=10.0)
        except Exception as exc:
            errors.append(f"trades {endpoint.rsplit('/', 1)[-1]}: {exc}")
            continue
        for item in payload.get("trades") or [] if isinstance(payload, dict) else []:
            if isinstance(item, dict):
                trades.append(normalize_trade(item))
    return sorted(trades, key=lambda item: str(item.get("created_time") or ""), reverse=True)[:limit]


def fetch_candles_for_window(
    ticker: str,
    *,
    start: datetime,
    end: datetime,
    cutoff: datetime | None,
    limit: int,
    period_interval_minutes: int,
    errors: list[str],
) -> list[dict[str, Any]]:
    segments = endpoint_segments(
        live_endpoint=f"{KALSHI_BASE}/markets/candlesticks",
        historical_endpoint=f"{HISTORICAL_BASE}/markets/{ticker}/candlesticks",
        start=start,
        end=end,
        cutoff=cutoff,
    )
    candles: list[dict[str, Any]] = []
    for endpoint, segment_start, segment_end in segments:
        params = {
            "start_ts": int(segment_start.timestamp()),
            "end_ts": int(segment_end.timestamp()),
            "period_interval": period_interval_minutes,
            "include_latest_before_start": "true",
        }
        if endpoint.endswith("/markets/candlesticks"):
            params["market_tickers"] = ticker
        try:
            payload = get_json(endpoint, params, timeout=10.0)
        except Exception as exc:
            errors.append(f"candlesticks {endpoint.rsplit('/', 1)[-1]}: {exc}")
            continue
        candles.extend(extract_candlesticks(payload))
    normalized = [normalize_candlestick(item) for item in candles]
    return sorted(normalized, key=lambda item: int(item.get("end_period_ts") or 0))[-limit:]


def endpoint_segments(
    *,
    live_endpoint: str,
    historical_endpoint: str,
    start: datetime,
    end: datetime,
    cutoff: datetime | None,
) -> list[tuple[str, datetime, datetime]]:
    if cutoff is None:
        return [(live_endpoint, start, end)]
    if end <= cutoff:
        return [(historical_endpoint, start, end)]
    if start >= cutoff:
        return [(live_endpoint, start, end)]
    return [
        (historical_endpoint, start, cutoff),
        (live_endpoint, cutoff, end),
    ]


def extract_candlesticks(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    if isinstance(payload.get("candlesticks"), list):
        return [item for item in payload["candlesticks"] if isinstance(item, dict)]
    markets = payload.get("markets")
    if not isinstance(markets, list):
        return []
    candles: list[dict[str, Any]] = []
    for market in markets:
        if isinstance(market, dict) and isinstance(market.get("candlesticks"), list):
            candles.extend(item for item in market["candlesticks"] if isinstance(item, dict))
    return candles


def normalize_trade(trade: dict[str, Any]) -> dict[str, Any]:
    return {
        "created_time": trade.get("created_time"),
        "yes_price": probability(trade.get("yes_price_dollars")),
        "no_price": probability(trade.get("no_price_dollars")),
        "count": number(trade.get("count_fp")),
        "taker_side": trade.get("taker_side") or trade.get("taker_outcome_side"),
        "taker_book_side": trade.get("taker_book_side"),
    }


def normalize_candlestick(candle: dict[str, Any]) -> dict[str, Any]:
    price = nested(candle, "price")
    yes_bid = nested(candle, "yes_bid")
    yes_ask = nested(candle, "yes_ask")
    return {
        "end_period_ts": candle.get("end_period_ts"),
        "price": dollars_ohlc(price),
        "yes_bid": dollars_ohlc(yes_bid),
        "yes_ask": dollars_ohlc(yes_ask),
        "volume": number(candle.get("volume_fp")),
        "open_interest": number(candle.get("open_interest_fp")),
    }


def dollars_ohlc(values: dict[str, Any]) -> dict[str, float | None]:
    return {
        "open": probability(values.get("open_dollars")),
        "high": probability(values.get("high_dollars")),
        "low": probability(values.get("low_dollars")),
        "close": probability(values.get("close_dollars")),
        "mean": probability(values.get("mean_dollars")),
        "previous": probability(values.get("previous_dollars")),
    }


def nested(value: dict[str, Any], key: str) -> dict[str, Any]:
    item = value.get(key)
    return item if isinstance(item, dict) else {}


def parse_iso_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    raw = str(value).strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)

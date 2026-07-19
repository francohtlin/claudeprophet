from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from market_lookup.dedupe import dedupe_markets
from market_lookup.providers.common import ProviderResult
from market_lookup.providers.kalshi_history import fetch_kalshi_market_history
from market_lookup.providers.kalshi import search_kalshi
from market_lookup.providers.polymarket_gamma import search_polymarket_gamma
from market_lookup.providers.pmxt_provider import search_pmxt


def lookup_markets(
    *,
    text: str,
    category: str | None = None,
    ideal_close_time: str | None = None,
    max_markets: int = 10,
    include_history: bool = False,
    history_lookback_days: int = 7,
    history_trade_limit: int = 50,
    history_candle_limit: int = 48,
    include_debug: bool = False,
) -> list[dict[str, Any]] | dict[str, Any]:
    """Return unique normalized market records.

    Only ``text`` is used for market retrieval. ``category`` and
    ``ideal_close_time`` are accepted so callers can keep a uniform event-shaped
    interface, but this lookup layer does not use them to infer relevance.
    """
    clean_text = text.strip()
    limit = max(1, min(int(max_markets), 50))
    debug: dict[str, Any] = {
        "input": {
            "text": clean_text,
            "category": category,
            "ideal_close_time": ideal_close_time,
            "max_markets": limit,
            "include_history": include_history,
            "history_lookback_days": history_lookback_days,
            "history_trade_limit": history_trade_limit,
            "history_candle_limit": history_candle_limit,
        },
        "providers": [],
    }

    markets: list[dict[str, Any]] = []

    gamma_result = search_polymarket_gamma(clean_text, max_markets=limit)
    markets.extend(gamma_result.markets)
    debug["providers"].append(gamma_result.debug)

    kalshi_result = search_kalshi(clean_text, max_markets=limit)
    markets.extend(kalshi_result.markets)
    debug["providers"].append(kalshi_result.debug)

    if os.getenv("MARKET_LOOKUP_ENABLE_PMXT") in {"1", "true", "TRUE", "yes", "YES"}:
        pmxt_result = search_pmxt(clean_text, max_markets=limit)
    else:
        pmxt_result = ProviderResult(
            markets=[],
            debug={
                "provider": "PMXT",
                "queries": [],
                "raw_counts": {"markets": 0},
                "normalized_count": 0,
                "sample_questions": [],
                "errors": [],
                "skipped": "disabled by default; set MARKET_LOOKUP_ENABLE_PMXT=1 to enable",
            },
        )
    markets.extend(pmxt_result.markets)
    debug["providers"].append(pmxt_result.debug)

    markets = annotate_close_time(markets, ideal_close_time)
    if include_history:
        markets = attach_kalshi_history(
            markets,
            lookback_days=history_lookback_days,
            trade_limit=history_trade_limit,
            candle_limit=history_candle_limit,
        )
    markets = strip_private_fields(order_markets_for_agent(dedupe_markets(markets)))[:limit]

    if include_debug:
        return {
            "agent_output": markets,
            "debug": debug,
        }
    return markets


def attach_kalshi_history(
    markets: list[dict[str, Any]],
    *,
    lookback_days: int,
    trade_limit: int,
    candle_limit: int,
) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for market in markets:
        item = dict(market)
        if item.get("source") != "Kalshi":
            enriched.append(item)
            continue
        ticker = str((item.get("_dedupe") or {}).get("market_id") or "")
        if ticker:
            item["history"] = fetch_kalshi_market_history(
                ticker,
                lookback_days=lookback_days,
                trade_limit=trade_limit,
                candle_limit=candle_limit,
            )
        enriched.append(item)
    return enriched


def annotate_close_time(markets: list[dict[str, Any]], ideal_close_time: str | None) -> list[dict[str, Any]]:
    ideal = parse_datetime(ideal_close_time)
    annotated: list[dict[str, Any]] = []
    for market in markets:
        item = dict(market)
        close_time = parse_datetime(item.get("close_time"))
        item["closes_before_ideal_close_time"] = (
            close_time < ideal if close_time is not None and ideal is not None else None
        )
        annotated.append(item)
    return annotated


def parse_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
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


def strip_private_fields(markets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for market in markets:
        item = dict(market)
        item.pop("_dedupe", None)
        item.pop("_retrieval_hits", None)
        cleaned.append(item)
    return cleaned


def order_by_close_time_fit(markets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Prefer markets that do not close before the requested ideal close time.

    This is not a relevance score. It is a factual ordering over the explicit
    field the caller asked us to compute, so stale markets do not consume the
    entire max_markets budget when current markets are also available.
    """
    close_order = {False: 0, None: 1, True: 2}
    return [
        market
        for _, market in sorted(
        enumerate(markets),
        key=lambda item: (
            market_status_tier(item[1]),
            close_order.get(item[1].get("closes_before_ideal_close_time"), 1),
            -market_liquidity_value(item[1]),
            item[0],
        ),
    )
    ]


def order_markets_for_agent(markets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Order factual records without adding scores or ranks to the output.

    We first prefer markets whose close time is still compatible with the
    requested ideal close time, then round-robin sources within each group so a
    single provider cannot consume the whole max_markets window.
    """
    ordered = order_by_close_time_fit(markets)
    result: list[dict[str, Any]] = []
    for status_tier in (0, 1, 2, 3):
        for close_flag in (False, None, True):
            group = [
                market
                for market in ordered
                if market_status_tier(market) == status_tier
                and market.get("closes_before_ideal_close_time") is close_flag
            ]
            result.extend(interleave_sources(group))
    return result


def interleave_sources(markets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_source: dict[str, list[dict[str, Any]]] = {}
    source_order: list[str] = []
    for market in markets:
        source = str(market.get("source") or "Unknown")
        if source not in by_source:
            by_source[source] = []
            source_order.append(source)
        by_source[source].append(market)

    result: list[dict[str, Any]] = []
    while any(by_source[source] for source in source_order):
        for source in source_order:
            if by_source[source]:
                result.append(by_source[source].pop(0))
    return result


def market_liquidity_value(market: dict[str, Any]) -> float:
    liquidity = market.get("liquidity") or {}
    for key in ("liquidity", "volume_24h", "volume", "open_interest"):
        value = liquidity.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return 0.0


def market_status_tier(market: dict[str, Any]) -> int:
    status_order = {"open": 0, "active": 0, "inactive": 1, "closed": 2, "archived": 3}
    return status_order.get(str(market.get("status") or "").lower(), 1)

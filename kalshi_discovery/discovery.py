from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from market_lookup.providers.common import get_json
from market_lookup.providers.kalshi import (
    KALSHI_BASE,
    category_hints,
    fetch_kalshi_series_payload,
    meaningful_terms,
    normalize_kalshi_market,
    term_hits,
)


def discover_kalshi(
    *,
    query: str = "",
    status: str = "open",
    series_ticker: str | None = None,
    event_ticker: str | None = None,
    category: str | None = None,
    max_pages: int = 10,
    max_series: int = 10,
    limit_per_page: int = 1000,
    max_markets: int = 250,
    include_orderbook: bool = False,
    orderbook_depth: int = 10,
    include_raw: bool = False,
) -> dict[str, Any]:
    clean_query = query.strip()
    page_limit = max(1, min(int(limit_per_page), 1000))
    output_limit = max(1, min(int(max_markets), 10000))
    page_cap = max(0, int(max_pages))
    series_cap = max(0, int(max_series))
    debug: dict[str, Any] = {"queries": [], "errors": []}

    raw_markets: list[dict[str, Any]] = []
    base_params: dict[str, Any] = {"limit": page_limit}
    if normalize_status_filter(status) is not None:
        base_params["status"] = normalize_status_filter(status)
    if series_ticker:
        base_params["series_ticker"] = series_ticker
    if event_ticker:
        base_params["event_ticker"] = event_ticker

    raw_markets.extend(fetch_all_markets(debug, base_params, max_pages=page_cap))

    for ticker in discover_series_from_category(debug, category, clean_query, max_series=series_cap):
        params = dict(base_params)
        params["series_ticker"] = ticker
        raw_markets.extend(fetch_all_markets(debug, params, max_pages=page_cap))

    raw_markets = dedupe_raw(raw_markets)
    normalized = [normalize_for_discovery(item) for item in raw_markets]
    if clean_query:
        terms = meaningful_terms(clean_query)
        normalized = [market for market in normalized if normalized_market_matches(market, terms)]

    markets = normalized[:output_limit]
    if include_orderbook:
        add_orderbooks(markets, debug, depth=max(1, min(int(orderbook_depth), 100)))
    result: dict[str, Any] = {
        "tool": "kalshi_discovery",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input": {
            "query": clean_query,
            "status": status,
            "series_ticker": series_ticker,
            "event_ticker": event_ticker,
            "category": category,
            "max_pages": page_cap,
            "max_series": series_cap,
            "limit_per_page": page_limit,
            "max_markets": output_limit,
            "include_orderbook": include_orderbook,
            "orderbook_depth": max(1, min(int(orderbook_depth), 100)),
        },
        "counts": {
            "raw_markets": len(raw_markets),
            "returned_markets": len(markets),
            "pages_requested": len([q for q in debug["queries"] if q.get("endpoint") == "/markets"]),
        },
        "markets": markets,
        "debug": debug,
    }
    if include_raw:
        result["raw_markets"] = raw_markets[:output_limit]
    return result


def normalize_for_discovery(raw: dict[str, Any]) -> dict[str, Any]:
    market = normalize_kalshi_market(raw)
    market.pop("_dedupe", None)
    market["ticker"] = raw.get("ticker")
    market["event_ticker"] = raw.get("event_ticker")
    market["series_ticker"] = raw.get("series_ticker")
    market["market_type"] = raw.get("market_type")
    market["notional_value"] = raw.get("notional_value")
    return market


def add_orderbooks(markets: list[dict[str, Any]], debug: dict[str, Any], *, depth: int) -> None:
    for market in markets:
        ticker = market.get("ticker")
        if not ticker:
            market["orderbook"] = {"error": "missing ticker"}
            continue
        params = {"depth": depth}
        endpoint = f"/markets/{ticker}/orderbook"
        debug["queries"].append({"endpoint": endpoint, **params})
        try:
            payload = get_json(f"{KALSHI_BASE}{endpoint}", params, timeout=10.0)
        except Exception as exc:
            market["orderbook"] = {"error": str(exc)}
            debug["errors"].append({"endpoint": endpoint, "error": str(exc)})
            continue
        market["orderbook"] = normalize_orderbook(payload)


def normalize_orderbook(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"raw": payload}
    orderbook = payload.get("orderbook") if isinstance(payload.get("orderbook"), dict) else payload
    return {
        "yes": orderbook.get("yes") or orderbook.get("yes_orders") or [],
        "no": orderbook.get("no") or orderbook.get("no_orders") or [],
    }


def fetch_all_markets(debug: dict[str, Any], params: dict[str, Any], *, max_pages: int) -> list[dict[str, Any]]:
    markets: list[dict[str, Any]] = []
    cursor = ""
    page = 0
    while True:
        if max_pages and page >= max_pages:
            break
        request_params = dict(params)
        if cursor:
            request_params["cursor"] = cursor
        debug["queries"].append({"endpoint": "/markets", **request_params})
        try:
            payload = get_json(f"{KALSHI_BASE}/markets", request_params, timeout=20.0)
        except Exception as exc:
            debug["errors"].append({"endpoint": "/markets", "params": request_params, "error": str(exc)})
            break
        page += 1
        page_markets = payload.get("markets") if isinstance(payload, dict) else None
        if isinstance(page_markets, list):
            markets.extend(item for item in page_markets if isinstance(item, dict))
        cursor = str(payload.get("cursor") or "") if isinstance(payload, dict) else ""
        if not cursor:
            break
    return markets


def discover_series_from_category(
    debug: dict[str, Any],
    category: str | None,
    query: str,
    *,
    max_series: int,
) -> list[str]:
    categories = [category] if category else category_hints(query)
    tickers: list[str] = []
    for category_name in [item for item in categories if item]:
        payload = fetch_kalshi_series_payload(debug, {"category": category_name})
        if not isinstance(payload, dict):
            continue
        for series in payload.get("series") or []:
            if not isinstance(series, dict):
                continue
            ticker = str(series.get("ticker") or "")
            if ticker and ticker not in tickers:
                tickers.append(ticker)
                if max_series and len(tickers) >= max_series:
                    return tickers
    return tickers


def normalized_market_matches(market: dict[str, Any], query_terms: list[str]) -> bool:
    if not query_terms:
        return True
    text_blob = " ".join(
        str(value or "")
        for value in (
            market.get("question"),
            market.get("description"),
            market.get("rules"),
            market.get("status"),
            market.get("close_time"),
            market.get("resolution_time"),
        )
    )
    return term_hits(text_blob, query_terms) >= min(2, len(set(query_terms)))


def normalize_status_filter(status: str) -> str | None:
    cleaned = str(status or "").strip().lower()
    if cleaned in {"", "all", "any", "*"}:
        return None
    return cleaned


def dedupe_raw(markets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for market in markets:
        key = str(market.get("ticker") or market.get("market_id") or "")
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        unique.append(market)
    return unique

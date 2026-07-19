from __future__ import annotations

from typing import Any

from market_lookup.providers.common import (
    ProviderResult,
    compact_text,
    get_json,
    midpoint,
    number,
    probability,
    spread,
)
from market_lookup.providers.polymarket_gamma import (
    market_matches_query,
    meaningful_terms,
    min_required_hits,
    term_hits,
)


KALSHI_BASE = "https://external-api.kalshi.com/trade-api/v2"
SERIES_PAGE_LIMIT = 200
GENERAL_PAGE_LIMIT = 100
SERIES_CACHE: dict[str, dict[str, Any]] = {}


def search_kalshi(text: str, *, max_markets: int = 10, pages: int = 1) -> ProviderResult:
    debug: dict[str, Any] = {
        "provider": "Kalshi",
        "queries": [],
        "raw_counts": {},
        "errors": [],
    }
    query_terms = meaningful_terms(text)
    if not query_terms:
        debug["raw_counts"]["markets"] = 0
        debug["normalized_count"] = 0
        return ProviderResult(markets=[], debug=debug)

    raw_items: list[dict[str, Any]] = []
    series_tickers = series_hints(text)
    series_tickers.extend(discover_series_tickers(debug, text))
    for series_ticker in list(dict.fromkeys(series_tickers)):
        raw_items.extend(
            fetch_kalshi_markets(
                debug,
                {"limit": SERIES_PAGE_LIMIT, "status": "open", "series_ticker": series_ticker},
            )
        )

    cursor = ""
    for page in range(max(1, pages)):
        params = {
            "limit": GENERAL_PAGE_LIMIT,
            "status": "open",
        }
        if cursor:
            params["cursor"] = cursor
        payload = fetch_kalshi_payload(debug, params)
        if payload is None:
            break
        raw_items.extend(markets_from_payload(payload))
        cursor = str(payload.get("cursor") or "")
        if not cursor:
            break

    raw_items = dedupe_raw_markets(raw_items)
    debug["raw_counts"]["markets"] = len(raw_items)
    normalized = []
    for item in raw_items:
        market = normalize_kalshi_market(item)
        if market_matches_query(market, text):
            normalized.append(market)
    debug["normalized_count"] = len(normalized)
    debug["sample_questions"] = [market.get("question") for market in normalized[:8]]
    return ProviderResult(markets=normalized[:max_markets], debug=debug)


def fetch_kalshi_markets(debug: dict[str, Any], params: dict[str, Any]) -> list[dict[str, Any]]:
    payload = fetch_kalshi_payload(debug, params)
    if payload is None:
        return []
    return markets_from_payload(payload)


def fetch_kalshi_payload(debug: dict[str, Any], params: dict[str, Any]) -> dict[str, Any] | None:
    debug["queries"].append({"endpoint": "/markets", **params})
    try:
        payload = get_json(f"{KALSHI_BASE}/markets", params)
    except Exception as exc:
        debug["errors"].append({"endpoint": "/markets", "error": str(exc), "params": params})
        return None
    return payload if isinstance(payload, dict) else None


def markets_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    page_markets = payload.get("markets") or []
    if not isinstance(page_markets, list):
        return []
    return [item for item in page_markets if isinstance(item, dict)]


def dedupe_raw_markets(markets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for market in markets:
        ticker = str(market.get("ticker") or "")
        if ticker and ticker in seen:
            continue
        if ticker:
            seen.add(ticker)
        unique.append(market)
    return unique


def discover_series_tickers(debug: dict[str, Any], text: str, *, max_series: int = 5) -> list[str]:
    tickers: list[str] = []
    query_terms = meaningful_terms(text)
    for category in category_hints(text):
        payload = fetch_kalshi_series_payload(debug, {"category": category})
        if payload is None:
            continue
        candidates: list[tuple[int, str]] = []
        for series in payload.get("series") or []:
            if not isinstance(series, dict):
                continue
            ticker = str(series.get("ticker") or "")
            if not ticker:
                continue
            text_blob = " ".join(
                str(value or "")
                for value in (
                    series.get("ticker"),
                    series.get("title"),
                    series.get("category"),
                    " ".join(str(tag) for tag in series.get("tags") or []),
                )
            )
            hits = term_hits(text_blob, query_terms)
            if hits >= min_required_hits(text):
                candidates.append((hits, ticker))
        candidates.sort(key=lambda item: (-item[0], item[1]))
        tickers.extend(ticker for _, ticker in candidates[:max_series])
    return tickers


def fetch_kalshi_series_payload(debug: dict[str, Any], params: dict[str, Any]) -> dict[str, Any] | None:
    debug["queries"].append({"endpoint": "/series", **params})
    cache_key = str(sorted(params.items()))
    if cache_key in SERIES_CACHE:
        return SERIES_CACHE[cache_key]
    try:
        payload = get_json(f"{KALSHI_BASE}/series", params, timeout=5.0)
    except Exception as exc:
        debug["errors"].append({"endpoint": "/series", "error": str(exc), "params": params})
        return None
    if not isinstance(payload, dict):
        return None
    SERIES_CACHE[cache_key] = payload
    return payload


def series_hints(text: str) -> list[str]:
    lowered = text.lower()
    hints: list[str] = []
    if any(term in lowered for term in ("fed", "fomc", "federal reserve", "interest rate", "rates")):
        hints.append("KXFED")
    if any(term in lowered for term in ("bitcoin", "btc")):
        hints.extend(["KXBTC", "KXBTCD"])
    if any(term in lowered for term in ("s&p", "sp500", "s&p 500", "spx")):
        hints.append("KXINX")
    return hints


def category_hints(text: str) -> list[str]:
    lowered = text.lower()
    hints: list[str] = []
    if any(
        term in lowered
        for term in (
            "election",
            "president",
            "presidential",
            "senate",
            "house",
            "governor",
            "mayor",
            "nominee",
            "democrat",
            "republican",
            "gop",
        )
    ):
        hints.append("Elections")
    if any(
        term in lowered
        for term in (
            "nba",
            "nfl",
            "mlb",
            "nhl",
            "champions league",
            "world cup",
            "super bowl",
            "basketball",
            "football",
            "baseball",
            "hockey",
            "soccer",
            "ufc",
            "tennis",
        )
    ):
        hints.append("Sports")
    if any(
        term in lowered
        for term in (
            "bitcoin",
            "btc",
            "ethereum",
            "eth",
            "crypto",
            "doge",
            "solana",
            "xrp",
            "token",
        )
    ):
        hints.append("Crypto")
    if any(
        term in lowered
        for term in (
            "fed",
            "fomc",
            "federal reserve",
            "interest rate",
            "rates",
            "cpi",
            "inflation",
            "unemployment",
            "gdp",
            "gas price",
            "mortgage",
        )
    ):
        hints.append("Economics")
    if any(term in lowered for term in ("s&p", "sp500", "s&p 500", "spx", "nasdaq", "dow jones", "stock market")):
        hints.append("Financials")
    return hints


def kalshi_market_matches(market: dict[str, Any], query_terms: list[str]) -> bool:
    question = kalshi_question(market)
    text_blob = " ".join(
        str(value or "")
        for value in (
            question,
            market.get("rules_primary"),
            market.get("rules_secondary"),
            market.get("event_ticker"),
            market.get("ticker"),
        )
    )
    hits = term_hits(text_blob, query_terms)
    return hits >= min(2, len(set(query_terms)))


def normalize_kalshi_market(market: dict[str, Any]) -> dict[str, Any]:
    yes_bid = probability(market.get("yes_bid_dollars"))
    yes_ask = probability(market.get("yes_ask_dollars"))
    no_bid = probability(market.get("no_bid_dollars"))
    no_ask = probability(market.get("no_ask_dollars"))
    return {
        "source": "Kalshi",
        "question": compact_text(kalshi_question(market)),
        "description": compact_text(market.get("title")),
        "rules": compact_text(kalshi_rules(market), max_chars=3000),
        "outcomes": ["Yes", "No"],
        "status": normalize_status(market.get("status")),
        "close_time": market.get("close_time"),
        "resolution_time": market.get("expiration_time") or market.get("expected_expiration_time"),
        "prices": {
            "yes_bid": yes_bid,
            "yes_ask": yes_ask,
            "no_bid": no_bid,
            "no_ask": no_ask,
            "last_price": probability(market.get("last_price_dollars")),
            "midpoint": midpoint(yes_bid, yes_ask),
            "spread": spread(yes_bid, yes_ask),
        },
        "liquidity": {
            "volume": number(market.get("volume_fp")),
            "volume_24h": number(market.get("volume_24h_fp")),
            "liquidity": number(market.get("liquidity_dollars")),
            "open_interest": number(market.get("open_interest_fp")),
        },
        "_dedupe": {
            "market_id": market.get("ticker"),
            "slug": market.get("ticker"),
        },
    }


def kalshi_question(market: dict[str, Any]) -> str:
    title = str(market.get("title") or "").strip()
    yes_sub_title = str(market.get("yes_sub_title") or "").strip()
    if yes_sub_title and yes_sub_title.lower() not in title.lower():
        return f"{title} - {yes_sub_title}"
    return title


def kalshi_rules(market: dict[str, Any]) -> str:
    primary = str(market.get("rules_primary") or "").strip()
    secondary = str(market.get("rules_secondary") or "").strip()
    if primary and secondary:
        return f"{primary}\n\n{secondary}"
    return primary or secondary or str(market.get("title") or "").strip()


def normalize_status(value: Any) -> str:
    raw = str(value or "").lower()
    if raw == "active":
        return "open"
    return raw or "unknown"

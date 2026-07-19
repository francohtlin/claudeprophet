from __future__ import annotations

import re
from typing import Any

from market_lookup.providers.common import ProviderResult, compact_text, midpoint, number, probability, spread


def search_pmxt(text: str, *, max_markets: int = 10) -> ProviderResult:
    """Optional PMXT lookup.

    PMXT is useful as a future multi-venue provider, but this wrapper is
    intentionally best-effort. Provider failure is returned in debug only and
    never leaks into the agent-facing market list.
    """
    debug: dict[str, Any] = {
        "provider": "PMXT",
        "queries": [],
        "raw_counts": {},
        "errors": [],
    }
    try:
        from pmxt import Polymarket  # type: ignore

        exchange = Polymarket()
        raw_items: list[Any] = []
        for query in pmxt_query_variants(text):
            debug["queries"].append({"exchange": "Polymarket", "method": "fetch_markets", "query": query})
            try:
                raw_markets = exchange.fetch_markets({"query": query, "limit": max_markets})
            except Exception as exc:
                debug["errors"].append({"query": query, "error": str(exc)})
                continue
            raw_items.extend(list(raw_markets))
            if len(raw_items) >= max_markets:
                break
        debug["raw_counts"]["markets"] = len(raw_items)
        markets = filter_by_query_terms(
            [normalize_pmxt_market(item) for item in raw_items],
            text,
        )[:max_markets]
        debug["normalized_count"] = len(markets)
        debug["sample_questions"] = [market.get("question") for market in markets[:8]]
        return ProviderResult(markets=markets, debug=debug)
    except Exception as exc:
        debug["errors"].append({"error": str(exc)})
        debug["raw_counts"]["markets"] = 0
        debug["normalized_count"] = 0
        return ProviderResult(markets=[], debug=debug)


def pmxt_query_variants(text: str) -> list[str]:
    variants: list[str] = []
    add_variant(variants, text)

    capitalized_tokens = re.findall(r"\b[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)*\b", text)
    for phrase in capitalized_tokens:
        words = phrase.split()
        if phrase.lower() in PMXT_STOP_WORDS:
            continue
        if len(words) >= 2:
            add_variant(variants, " ".join(words[:2]))
        add_variant(variants, phrase)

    tokens = [
        token.strip(".,:;!?()[]{}\"'")
        for token in text.split()
        if len(token.strip(".,:;!?()[]{}\"'")) >= 3
    ]
    keywords = [token for token in tokens if token.lower() not in PMXT_STOP_WORDS]
    if len(keywords) >= 3:
        add_variant(variants, " ".join(keywords[:3]))
    if len(keywords) >= 2:
        add_variant(variants, " ".join(keywords[:2]))

    return variants[:6]


def add_variant(variants: list[str], value: str) -> None:
    cleaned = " ".join(value.strip().split())
    if cleaned and cleaned not in variants:
        variants.append(cleaned)


PMXT_STOP_WORDS = {
    "will",
    "say",
    "during",
    "before",
    "after",
    "with",
    "from",
    "that",
    "this",
    "what",
    "when",
    "where",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
    "january",
    "february",
    "march",
    "april",
    "may",
}


def filter_by_query_terms(markets: list[dict[str, Any]], text: str) -> list[dict[str, Any]]:
    terms = [
        token.strip(".,:;!?()[]{}\"'").lower()
        for token in text.split()
        if len(token.strip(".,:;!?()[]{}\"'")) >= 3
        and token.strip(".,:;!?()[]{}\"'").lower() not in PMXT_STOP_WORDS
        and not token.strip(".,:;!?()[]{}\"'").isdigit()
    ]
    if not terms:
        return markets

    filtered: list[tuple[int, dict[str, Any]]] = []
    for market in markets:
        text_blob = " ".join(
            str(market.get(key) or "")
            for key in ("question", "description", "rules")
        ).lower()
        hits = sum(1 for term in set(terms) if term in text_blob)
        if hits >= min(2, len(set(terms))):
            filtered.append((hits, market))
    filtered.sort(key=lambda item: -item[0])
    return [market for _, market in filtered]


def normalize_pmxt_market(item: Any) -> dict[str, Any]:
    data = model_dump(item)
    question = data.get("question") or data.get("title") or data.get("symbol")
    outcomes = data.get("outcomes") or []
    if not isinstance(outcomes, list):
        outcomes = []
    yes_bid = probability(first_present(data, "yes_bid", "best_bid", "bid"))
    yes_ask = probability(first_present(data, "yes_ask", "best_ask", "ask"))
    return {
        "source": "Polymarket",
        "question": compact_text(question),
        "description": compact_text(data.get("description")),
        "rules": compact_text(data.get("rules") or data.get("description")),
        "outcomes": [outcome_label(outcome) for outcome in outcomes],
        "status": compact_text(data.get("status")) or "unknown",
        "close_time": isoish(data.get("close_time") or data.get("end_time") or data.get("expiration_time")),
        "resolution_time": isoish(data.get("resolution_time") or data.get("resolution_date")),
        "prices": {
            "yes_bid": yes_bid,
            "yes_ask": yes_ask,
            "no_bid": None if yes_ask is None else round(1 - yes_ask, 6),
            "no_ask": None if yes_bid is None else round(1 - yes_bid, 6),
            "last_price": probability(first_present(data, "last_price", "last_trade_price")) or outcome_price(data.get("yes")),
            "midpoint": midpoint(yes_bid, yes_ask) or outcome_price(data.get("yes")),
            "spread": spread(yes_bid, yes_ask),
        },
        "liquidity": {
            "volume": number(data.get("volume")),
            "volume_24h": number(data.get("volume_24h")),
            "liquidity": number(data.get("liquidity")),
            "open_interest": number(data.get("open_interest")),
        },
        "_dedupe": {
            "condition_id": data.get("condition_id"),
            "market_id": data.get("market_id") or data.get("id"),
            "slug": data.get("slug") or data.get("symbol"),
        },
    }


def model_dump(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        return item
    if hasattr(item, "model_dump"):
        dumped = item.model_dump()
        return dumped if isinstance(dumped, dict) else {}
    if hasattr(item, "dict"):
        dumped = item.dict()
        return dumped if isinstance(dumped, dict) else {}
    return {
        key: getattr(item, key)
        for key in dir(item)
        if not key.startswith("_") and not callable(getattr(item, key))
    }


def first_present(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = data.get(key)
        if value is not None:
            return value
    return None


def outcome_label(outcome: Any) -> str:
    data = model_dump(outcome)
    return str(data.get("label") or data.get("name") or outcome)


def outcome_price(outcome: Any) -> float | None:
    data = model_dump(outcome)
    return probability(data.get("price"))


def isoish(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)

from __future__ import annotations

import re
from typing import Any

from market_lookup.providers.common import (
    ProviderResult,
    compact_text,
    complement,
    get_json,
    midpoint,
    number,
    parse_jsonish_list,
    probability,
    spread,
    first_paragraph,
)


GAMMA_BASE = "https://gamma-api.polymarket.com"
TOKEN_RE = re.compile(r"[a-z0-9]+")


def search_polymarket_gamma(text: str, *, max_markets: int = 10) -> ProviderResult:
    debug: dict[str, Any] = {
        "provider": "Polymarket Gamma",
        "queries": [],
        "raw_counts": {},
        "errors": [],
    }
    markets: list[dict[str, Any]] = []

    query = text.strip()
    if not query:
        return ProviderResult(markets=[], debug=debug)

    try:
        payload = get_json(
            f"{GAMMA_BASE}/public-search",
            {"q": query, "limit": max_markets},
        )
        debug["queries"].append({"endpoint": "/public-search", "q": query})
        debug["raw_counts"]["public_search_events"] = len(payload.get("events") or [])
        debug["raw_counts"]["public_search_markets"] = len(payload.get("markets") or [])
        markets.extend(markets_from_public_search(payload, query))
    except Exception as exc:  # Provider failures should not break prediction.
        debug["errors"].append({"endpoint": "/public-search", "error": str(exc)})

    try:
        payload = get_json(
            f"{GAMMA_BASE}/markets",
            {"query": query, "limit": max_markets},
        )
        debug["queries"].append({"endpoint": "/markets", "query": query})
        debug["raw_counts"]["markets"] = len(payload or []) if isinstance(payload, list) else 0
        if isinstance(payload, list):
            for market in payload:
                normalized = normalize_market(market, parent_event=None)
                if market_matches_query(normalized, query):
                    markets.append(normalized)
    except Exception as exc:
        debug["errors"].append({"endpoint": "/markets", "error": str(exc)})

    debug["normalized_count"] = len(markets)
    debug["sample_questions"] = [
        market.get("question")
        for market in markets[: min(8, len(markets))]
    ]
    return ProviderResult(markets=markets, debug=debug)


def markets_from_public_search(payload: dict[str, Any], query: str) -> list[dict[str, Any]]:
    markets: list[dict[str, Any]] = []
    query_terms = meaningful_terms(query)

    for market in payload.get("markets") or []:
        normalized = normalize_market(market, parent_event=None)
        if market_matches_query(normalized, query):
            markets.append(normalized)

    for event in payload.get("events") or []:
        event_markets = event.get("markets") or []
        if not isinstance(event_markets, list):
            continue
        matched: list[dict[str, Any]] = []
        for market in event_markets:
            normalized = normalize_market(market, parent_event=event)
            hits = market_retrieval_hits(normalized, query_terms)
            if market_matches_query(normalized, query, hits=hits):
                normalized["_retrieval_hits"] = hits
                matched.append(normalized)
        matched.sort(
            key=lambda item: (
                -(item.get("_retrieval_hits") or {}).get("question_hits", 0),
                -(item.get("_retrieval_hits") or {}).get("broad_hits", 0),
            )
        )
        for item in matched:
            item.pop("_retrieval_hits", None)
        markets.extend(matched)

    return markets


def term_hits(value: str, terms: list[str]) -> int:
    haystack = set(text_tokens(value))
    return sum(1 for term in set(terms) if term in haystack)


def meaningful_terms(query: str) -> list[str]:
    return expand_terms(base_meaningful_terms(query))


def base_meaningful_terms(query: str) -> list[str]:
    stop = {
        "will",
        "the",
        "and",
        "or",
        "a",
        "an",
        "to",
        "of",
        "in",
        "on",
        "at",
        "by",
        "for",
        "during",
        "before",
        "after",
        "what",
        "who",
        "when",
        "where",
        "is",
        "are",
        "be",
        "it",
        "this",
        "that",
        "with",
        "as",
    }
    short_keep = {"ai", "eu", "uk", "us", "mlb", "nba", "nfl", "nhl", "btc", "eth"}
    return [
        term
        for term in text_tokens(query)
        if (len(term) >= 3 or term in short_keep) and term not in stop
    ]


def expand_terms(terms: list[str]) -> list[str]:
    expanded: list[str] = []
    term_set = set(terms)
    aliases = {
        "presidential": ["president"],
        "president": ["presidential"],
        "democrats": ["democrat", "democratic"],
        "democrat": ["democrats", "democratic"],
        "democratic": ["democrat", "democrats"],
        "republicans": ["republican", "gop"],
        "republican": ["republicans", "gop"],
        "gop": ["republican", "republicans"],
        "governor": ["gubernatorial"],
        "gubernatorial": ["governor"],
        "bitcoin": ["btc"],
        "btc": ["bitcoin"],
        "ethereum": ["eth", "ether"],
        "eth": ["ethereum", "ether"],
        "basketball": ["nba"],
        "football": ["nfl"],
        "baseball": ["mlb"],
        "hockey": ["nhl"],
        "soccer": ["football"],
    }
    if "united" in term_set and "states" in term_set:
        expanded.append("us")
    for term in terms:
        expanded.append(term)
        expanded.extend(aliases.get(term, []))
        if term.endswith("ies") and len(term) > 4:
            expanded.append(term[:-3] + "y")
        elif term.endswith("s") and len(term) > 4:
            expanded.append(term[:-1])
    return list(dict.fromkeys(expanded))


def min_required_hits(query: str) -> int:
    unique_terms = set(base_meaningful_terms(query))
    if len(unique_terms) <= 1:
        return 1
    if len(unique_terms) >= 4:
        return 3
    return min(2, len(unique_terms))


def text_tokens(value: str) -> list[str]:
    normalized = value.lower().replace("u.s.", "us")
    normalized = re.sub(r"(?<=\d),(?=\d)", "", normalized)
    return TOKEN_RE.findall(normalized)


def important_numeric_terms(query: str) -> list[str]:
    return [
        term
        for term in text_tokens(query)
        if (term.isdigit() and len(term) >= 4) or (term.isdigit() and int(term) >= 100)
    ]


def market_retrieval_hits(market: dict[str, Any], query_terms: list[str]) -> dict[str, int]:
    question = str(market.get("question") or "")
    haystack = " ".join(
        str(market.get(key) or "")
        for key in ("question", "description", "rules")
    )
    return {
        "question_hits": term_hits(question, query_terms),
        "broad_hits": term_hits(haystack, query_terms),
    }


def market_matches_query(market: dict[str, Any], query: str, *, hits: dict[str, int] | None = None) -> bool:
    query_terms = meaningful_terms(query)
    hits = hits or market_retrieval_hits(market, query_terms)
    if hits["broad_hits"] < min_required_hits(query):
        return False

    haystack = " ".join(
        str(market.get(key) or "")
        for key in ("question", "description", "rules", "close_time")
    )
    market_tokens = set(text_tokens(haystack))

    for group in required_term_groups(query):
        if not any(term in market_tokens for term in group):
            return False

    required_numbers = important_numeric_terms(query)
    if required_numbers:
        year_terms = [term for term in required_numbers if len(term) == 4 and term.startswith("20")]
        large_terms = [term for term in required_numbers if term not in year_terms]
        if year_terms and not any(term in market_tokens for term in year_terms):
            return False
        if large_terms and not all(term in market_tokens for term in large_terms):
            return False
    return True


def required_term_groups(query: str) -> list[set[str]]:
    base_terms = base_meaningful_terms(query)
    tokens = set(base_terms)
    groups: list[set[str]] = []
    if "house" in tokens:
        groups.append({"house"})
    if "senate" in tokens:
        groups.append({"senate"})
    if "governor" in tokens or "gubernatorial" in tokens:
        groups.append({"governor", "gubernatorial"})
    if "mayor" in tokens or "mayoral" in tokens:
        groups.append({"mayor", "mayoral"})
    if "president" in tokens or "presidential" in tokens:
        groups.append({"president", "presidential"})
    if "cpi" in tokens:
        groups.append({"cpi"})
    if "fed" in tokens or "fomc" in tokens:
        groups.append({"fed", "federal", "fomc"})
    if "bitcoin" in tokens or "btc" in tokens:
        groups.append({"bitcoin", "btc"})
    specific_terms = [
        term
        for term in base_terms
        if term not in generic_market_terms() and not term.isdigit()
    ]
    if len(specific_terms) >= 2:
        groups.append(set(expand_terms(specific_terms)))
    return groups


def generic_market_terms() -> set[str]:
    return {
        "above",
        "after",
        "before",
        "below",
        "between",
        "control",
        "day",
        "december",
        "election",
        "elections",
        "final",
        "finals",
        "game",
        "games",
        "hit",
        "january",
        "june",
        "march",
        "market",
        "may",
        "midterm",
        "midterms",
        "next",
        "party",
        "percent",
        "price",
        "reach",
        "result",
        "win",
        "winner",
        "world",
        "year",
        "mlb",
        "nba",
        "nfl",
        "nhl",
    }


def normalize_market(market: dict[str, Any], parent_event: dict[str, Any] | None) -> dict[str, Any]:
    outcomes = [str(item) for item in parse_jsonish_list(market.get("outcomes"))]
    outcome_prices = parse_jsonish_list(market.get("outcomePrices"))
    yes_price = probability(outcome_prices[0]) if outcome_prices else None
    no_price = probability(outcome_prices[1]) if len(outcome_prices) > 1 else None
    yes_bid = probability(market.get("bestBid"))
    yes_ask = probability(market.get("bestAsk"))
    if yes_bid is None and yes_price is not None:
        yes_bid = yes_price
    if yes_ask is None and yes_price is not None:
        yes_ask = yes_price
    no_bid = complement(yes_ask)
    no_ask = complement(yes_bid)
    if no_price is not None and no_bid is None:
        no_bid = no_price
    if no_price is not None and no_ask is None:
        no_ask = no_price

    closed = bool(market.get("closed") or (parent_event or {}).get("closed"))
    active = bool(market.get("active") or (parent_event or {}).get("active"))
    archived = bool(market.get("archived") or (parent_event or {}).get("archived"))
    if archived:
        status = "archived"
    elif closed:
        status = "closed"
    elif active:
        status = "open"
    else:
        status = "inactive"

    raw_description = market.get("description") or (parent_event or {}).get("description")
    description = first_paragraph(raw_description)
    rules = compact_text(
        market.get("rules")
        or market.get("resolutionCriteria")
        or market.get("resolutionSource")
        or raw_description,
        max_chars=3000,
    )

    return {
        "source": "Polymarket",
        "question": compact_text(market.get("question") or market.get("title")),
        "description": description,
        "rules": rules,
        "outcomes": outcomes,
        "status": status,
        "close_time": market.get("endDate") or (parent_event or {}).get("endDate"),
        "resolution_time": market.get("closedTime") or market.get("umaEndDate"),
        "prices": {
            "yes_bid": yes_bid,
            "yes_ask": yes_ask,
            "no_bid": no_bid,
            "no_ask": no_ask,
            "last_price": probability(market.get("lastTradePrice")) or yes_price,
            "midpoint": midpoint(yes_bid, yes_ask),
            "spread": spread(yes_bid, yes_ask),
        },
        "liquidity": {
            "volume": number(market.get("volumeNum") or market.get("volume")),
            "volume_24h": number(market.get("volume24hr") or market.get("volume24h")),
            "volume_1wk": number(market.get("volume1wk")),
            "volume_1mo": number(market.get("volume1mo")),
            "liquidity": number(market.get("liquidityNum") or market.get("liquidity")),
            "open_interest": number(market.get("openInterest")),
        },
        "_dedupe": {
            "condition_id": market.get("conditionId"),
            "market_id": market.get("id"),
            "slug": market.get("slug"),
        },
    }

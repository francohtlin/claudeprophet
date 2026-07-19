from __future__ import annotations

import re
from typing import Any


SPACE_RE = re.compile(r"\s+")
PUNCT_RE = re.compile(r"[^a-z0-9 ]+")


def dedupe_markets(markets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for market in markets:
        key = dedupe_key(market)
        if key in seen:
            continue
        seen.add(key)
        unique.append(market)
    return unique


def dedupe_key(market: dict[str, Any]) -> str:
    hidden = market.get("_dedupe") or {}
    condition_id = hidden.get("condition_id")
    if condition_id:
        return f"condition_id:{condition_id}"
    slug = hidden.get("slug")
    if slug:
        return f"slug:{normalize_text(str(slug))}"
    market_id = hidden.get("market_id")
    if market_id:
        return f"{market.get('source', 'unknown')}:market_id:{market_id}"
    question = normalize_text(str(market.get("question") or ""))
    close_time = str(market.get("close_time") or "")
    outcomes = ",".join(str(item).lower() for item in market.get("outcomes") or [])
    return f"{market.get('source', 'unknown')}:{question}:{close_time}:{outcomes}"


def normalize_text(value: str) -> str:
    value = value.lower()
    value = PUNCT_RE.sub(" ", value)
    return SPACE_RE.sub(" ", value).strip()

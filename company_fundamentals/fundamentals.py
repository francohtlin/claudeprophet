"""Discover open Kalshi company/KPI markets and attach issuer fundamentals.

This is a deterministic evidence tool (no LLM). It:
  1. enumerates Kalshi's ``Companies`` / ``Financials`` series (that category IS
     the company-market filter -- reliable, unlike page-scanning /markets, which
     is dominated by tens of thousands of sports parlays),
  2. selects the series matching the company/query (or symbols),
  3. fetches those series' open markets and annotates each with the KPI metric it
     references, and
  4. resolves the issuer ticker(s) and pulls their fundamentals via finance_lookup.

Ticker judgment is kept explicit: pass ``--symbols`` when you know them. Otherwise
the tool resolves from the series title via a curated alias map and reports what it
inferred (``symbols_were_inferred``) so the agent can override before trusting the
fundamentals. The probability forecast is done by the agent, not here.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from market_lookup.providers.common import get_json
from market_lookup.providers.kalshi import KALSHI_BASE, normalize_kalshi_market


DEFAULT_CATEGORIES: tuple[str, ...] = ("Companies", "Financials")

# Company-level KPI / corporate-event vocabulary, matched against real Kalshi
# company-market phrasing ("report above N total customers", "Headcount", ...).
# With category gating this is annotation, not a filter, so breadth is safe.
KPI_KEYWORDS: tuple[str, ...] = (
    "revenue",
    "sales",
    "earnings",
    "eps",
    "profit",
    "net income",
    "operating income",
    "margin",
    "guidance",
    "customers",
    "funded customers",
    "subscribers",
    "subscriber",
    "users",
    "active users",
    "downloads",
    "headcount",
    "employees",
    "deliveries",
    "units",
    "shipments",
    "stores",
    "bookings",
    "backlog",
    "market cap",
    "valuation",
    "dividend",
    "buyback",
    "ipo",
    "layoffs",
    "acquisition",
    "acquire",
    "merger",
    "bankruptcy",
    "ceo",
    "market share",
)

# Curated company -> primary ticker map (lowercase keys). High-precision on
# purpose; extend as new company series appear.
COMPANY_TICKER_ALIASES: dict[str, str] = {
    "nvidia": "NVDA",
    "apple": "AAPL",
    "microsoft": "MSFT",
    "google": "GOOGL",
    "alphabet": "GOOGL",
    "amazon": "AMZN",
    "meta": "META",
    "facebook": "META",
    "tesla": "TSLA",
    "netflix": "NFLX",
    "amd": "AMD",
    "intel": "INTC",
    "broadcom": "AVGO",
    "palantir": "PLTR",
    "oracle": "ORCL",
    "salesforce": "CRM",
    "adobe": "ADBE",
    "coinbase": "COIN",
    "robinhood": "HOOD",
    "uber": "UBER",
    "lyft": "LYFT",
    "airbnb": "ABNB",
    "spotify": "SPOT",
    "disney": "DIS",
    "walmart": "WMT",
    "costco": "COST",
    "starbucks": "SBUX",
    "domino": "DPZ",
    "boeing": "BA",
    "ford": "F",
    "general motors": "GM",
    "rivian": "RIVN",
}

_TICKER_STOPWORDS: frozenset[str] = frozenset(
    {"YES", "NO", "THE", "AND", "FOR", "WILL", "KX", "USD", "CEO", "CFO",
     "IPO", "EPS", "AI", "US", "Q1", "Q2", "Q3", "Q4", "FY", "GDP", "CPI", "INC"}
)

_TICKER_RE = re.compile(r"\b[A-Z]{1,5}\b")
_WORD_RE = re.compile(r"[a-z0-9]+")
_STOPTERMS: frozenset[str] = frozenset(
    {"will", "the", "and", "for", "report", "above", "below", "than", "in", "of",
     "q1", "q2", "q3", "q4", "inc", "corp", "company", "than", "more", "less"}
)


# --------------------------------------------------------------------------- #
# Pure, unit-testable helpers
# --------------------------------------------------------------------------- #
def query_terms(text: str) -> list[str]:
    return [w for w in _WORD_RE.findall((text or "").lower()) if len(w) > 1 and w not in _STOPTERMS]


def market_kpi_signals(market: dict[str, Any]) -> list[str]:
    blob = _market_text(market).lower()
    return [kw for kw in KPI_KEYWORDS if kw in blob]


def is_kpi_market(market: dict[str, Any]) -> bool:
    return bool(market_kpi_signals(market))


def filter_kpi_markets(markets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep markets with a KPI signal, annotating each with its matched signals."""
    kept: list[dict[str, Any]] = []
    for market in markets:
        signals = market_kpi_signals(market)
        if not signals:
            continue
        annotated = dict(market)
        annotated["kpi_signals"] = signals
        kept.append(annotated)
    return kept


def series_matches(series: dict[str, Any], terms: list[str], symbols: list[str]) -> bool:
    """A series is selected if its title/ticker matches the query terms or symbols."""
    if not terms and not symbols:
        return False
    ticker = str(series.get("ticker") or "").upper()
    title = str(series.get("title") or "").lower()
    for sym in symbols:
        if sym and sym.upper() in ticker:
            return True
    for term in terms:
        if term in title or term.upper() in ticker:
            return True
    return False


def extract_symbols_from_text(text: str) -> list[str]:
    """Best-effort ticker resolution from text: alias map first, then ticker-shaped tokens."""
    if not text:
        return []
    lowered = text.lower()
    found: list[str] = []
    for name, ticker in COMPANY_TICKER_ALIASES.items():
        if name in lowered and ticker not in found:
            found.append(ticker)
    for token in _TICKER_RE.findall(text):
        if token in _TICKER_STOPWORDS or len(token) < 2:
            continue
        if token not in found:
            found.append(token)
    return found


def symbols_from_series(series_list: list[dict[str, Any]]) -> list[str]:
    """Resolve tickers from series titles via the alias map (safer than ticker stripping)."""
    out: list[str] = []
    for series in series_list:
        title = str(series.get("title") or "").lower()
        for name, ticker in COMPANY_TICKER_ALIASES.items():
            if name in title and ticker not in out:
                out.append(ticker)
    return out


def resolve_symbols(
    explicit: list[str],
    *,
    query: str = "",
    markets: list[dict[str, Any]] | None = None,
    series_list: list[dict[str, Any]] | None = None,
) -> list[str]:
    """Explicit symbols win; else infer from query, then series titles, then market text."""
    if explicit:
        return _dedupe_upper(explicit)
    inferred = extract_symbols_from_text(query)
    if inferred:
        return _dedupe_upper(inferred)
    from_series = symbols_from_series(series_list or [])
    if from_series:
        return _dedupe_upper(from_series)
    market_blob = " ".join(_market_text(m) for m in (markets or []))
    return _dedupe_upper(extract_symbols_from_text(market_blob))


def parse_symbols(value: str | list[str] | None) -> list[str]:
    if value is None:
        return []
    parts = re.split(r"[,\s]+", value) if isinstance(value, str) else list(value)
    return _dedupe_upper(parts)


def parse_categories(value: str | list[str] | None) -> list[str]:
    if not value:
        return list(DEFAULT_CATEGORIES)
    parts = [p.strip() for p in value.split(",")] if isinstance(value, str) else list(value)
    return [p for p in parts if p]


# --------------------------------------------------------------------------- #
# Orchestration (network; best-effort, never raises)
# --------------------------------------------------------------------------- #
def discover_company_kpis(
    *,
    query: str = "",
    symbols: str | list[str] | None = None,
    categories: str | list[str] | None = None,
    status: str = "open",
    max_series: int = 25,
    max_markets_per_series: int = 100,
    lookback_days: int = 120,
    max_fundamental_items: int = 5,
    include_fundamentals: bool = True,
) -> dict[str, Any]:
    clean_query = query.strip()
    explicit_symbols = parse_symbols(symbols)
    target_categories = parse_categories(categories)
    terms = query_terms(clean_query)
    errors: list[dict[str, Any]] = []

    # 1. Enumerate company/financial series (deduped by ticker).
    all_series: list[dict[str, Any]] = []
    seen_series: set[str] = set()
    for category in target_categories:
        try:
            payload = get_json(f"{KALSHI_BASE}/series", {"category": category}, timeout=20.0)
            for series in payload.get("series") or []:
                if not isinstance(series, dict):
                    continue
                ticker = str(series.get("ticker") or "")
                if ticker and ticker in seen_series:
                    continue
                if ticker:
                    seen_series.add(ticker)
                all_series.append(series)
        except Exception as exc:  # pragma: no cover - network path
            errors.append({"stage": "series_list", "category": category, "error": str(exc)})

    # 2. Select matching series (or, with no filter, hand back a catalog).
    if not terms and not explicit_symbols:
        catalog = [
            {"series_ticker": s.get("ticker"), "title": s.get("title"), "category": s.get("category")}
            for s in all_series[:250]
        ]
        return _result(
            clean_query, explicit_symbols, target_categories, status,
            resolved=[], markets=[], fundamentals={}, series_selected=[],
            counts={"available_series": len(all_series), "series_selected": 0,
                    "returned_markets": 0, "resolved_symbols": 0},
            errors=errors, series_catalog=catalog,
            note=("No --query or --symbols given: returning the company/financial "
                  "series catalog. Re-run with a company name or --symbols to fetch "
                  "its open markets and fundamentals."),
        )

    selected = [s for s in all_series if series_matches(s, terms, explicit_symbols)][:max_series]

    # 3. Fetch each selected series' open markets.
    markets: list[dict[str, Any]] = []
    for series in selected:
        ticker = str(series.get("ticker") or "")
        if not ticker:
            continue
        try:
            payload = get_json(
                f"{KALSHI_BASE}/markets",
                {"series_ticker": ticker, "status": status, "limit": max(1, min(int(max_markets_per_series), 1000))},
                timeout=20.0,
            )
        except Exception as exc:  # pragma: no cover - network path
            errors.append({"stage": "series_markets", "series": ticker, "error": str(exc)})
            continue
        for raw in payload.get("markets") or []:
            if not isinstance(raw, dict):
                continue
            normalized = normalize_kalshi_market(raw)
            normalized.pop("_dedupe", None)
            normalized["ticker"] = raw.get("ticker")
            normalized["event_ticker"] = raw.get("event_ticker")
            normalized["series_ticker"] = ticker
            normalized["series_title"] = series.get("title")
            normalized["kpi_signals"] = market_kpi_signals(normalized)
            markets.append(normalized)

    # 4. Resolve tickers + fundamentals.
    resolved = resolve_symbols(explicit_symbols, query=clean_query, markets=markets, series_list=selected)
    fundamentals: dict[str, Any] = {}
    if include_fundamentals and resolved:
        try:
            from finance_lookup.lookup import lookup_finance

            fundamentals = lookup_finance(
                query=clean_query,
                symbols=resolved,
                asset_type="equity",
                data_needed="price,history,news,filings",
                lookback_days=lookback_days,
                max_items=max_fundamental_items,
            )
        except Exception as exc:  # pragma: no cover - network path
            errors.append({"stage": "finance_lookup", "error": str(exc)})

    return _result(
        clean_query, explicit_symbols, target_categories, status,
        resolved=resolved, markets=markets, fundamentals=fundamentals, series_selected=selected,
        counts={"available_series": len(all_series), "series_selected": len(selected),
                "returned_markets": len(markets), "resolved_symbols": len(resolved)},
        errors=errors,
    )


def _result(query, symbols, categories, status, *, resolved, markets, fundamentals,
            series_selected, counts, errors, series_catalog=None, note=None):
    result: dict[str, Any] = {
        "tool": "company_fundamentals",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input": {
            "query": query,
            "symbols": symbols,
            "categories": categories,
            "status": status,
        },
        "resolved_symbols": resolved,
        "symbols_were_inferred": not bool(symbols) and bool(resolved),
        "series_selected": [
            {"series_ticker": s.get("ticker"), "title": s.get("title")} for s in series_selected
        ],
        "kpi_markets": [_slim_market(m) for m in markets],
        "fundamentals": fundamentals,
        "counts": counts,
        "errors": errors,
        "notes": (note or
                  "Deterministic evidence only. When --symbols is omitted, ticker "
                  "resolution is best-effort from the series title; verify "
                  "resolved_symbols before relying on fundamentals. The probability "
                  "forecast is done by the agent, not here."),
    }
    if series_catalog is not None:
        result["series_catalog"] = series_catalog
    return result


def _market_text(market: dict[str, Any]) -> str:
    return " ".join(
        str(market.get(field) or "")
        for field in ("question", "description", "rules", "title", "series_title")
    )


def _slim_market(market: dict[str, Any]) -> dict[str, Any]:
    return {
        "ticker": market.get("ticker"),
        "event_ticker": market.get("event_ticker"),
        "series_ticker": market.get("series_ticker"),
        "series_title": market.get("series_title"),
        "question": market.get("question"),
        "status": market.get("status"),
        "close_time": market.get("close_time"),
        "resolution_time": market.get("resolution_time"),
        "prices": market.get("prices"),
        "liquidity": market.get("liquidity"),
        "kpi_signals": market.get("kpi_signals", []),
    }


def _dedupe_upper(values: list[str]) -> list[str]:
    out: list[str] = []
    for value in values:
        token = str(value or "").strip().upper()
        if token and token not in out:
            out.append(token)
    return out

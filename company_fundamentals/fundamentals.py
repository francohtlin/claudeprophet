"""Discover open Kalshi company/KPI markets and attach issuer fundamentals.

This is a deterministic evidence tool (no LLM). It:
  1. retrieves candidate open Kalshi markets (via kalshi_discovery),
  2. keeps the ones whose text is about company-level KPIs / corporate events,
  3. resolves the underlying stock ticker(s), and
  4. pulls fundamentals for those tickers (via finance_lookup).

The reference-class / ticker judgment is kept explicit: pass ``--symbols`` when
you know them. When you do not, the tool makes a best-effort resolution from a
small curated alias map plus the market text, and reports what it inferred so the
caller (the forecasting agent) can override.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any


# Company-level KPI / corporate-event vocabulary. A market whose text contains
# one of these is treated as a company-fundamentals market.
KPI_KEYWORDS: tuple[str, ...] = (
    "revenue",
    "sales",
    "earnings",
    "eps",
    "profit",
    "net income",
    "operating income",
    "gross margin",
    "margin",
    "guidance",
    "forecast",
    "subscribers",
    "subscriber",
    "active users",
    "daily active",
    "monthly active",
    "deliveries",
    "units sold",
    "shipments",
    "bookings",
    "backlog",
    "market cap",
    "valuation",
    "dividend",
    "buyback",
    "share repurchase",
    "ipo",
    "layoffs",
    "acquisition",
    "acquire",
    "merger",
    "bankruptcy",
    "chapter 11",
    "ceo",
    "market share",
)

# Curated company -> primary ticker map. Intentionally small and high-precision;
# extend as new company markets appear. Keys are lowercase.
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
    "starbucks": "SBUX",
    "boeing": "BA",
    "ford": "F",
    "gm": "GM",
    "general motors": "GM",
    "rivian": "RIVN",
}

# Uppercase tokens that look like tickers but are common words we should ignore
# when scraping free text for symbols.
_TICKER_STOPWORDS: frozenset[str] = frozenset(
    {
        "YES",
        "NO",
        "THE",
        "AND",
        "FOR",
        "WILL",
        "KX",
        "USD",
        "CEO",
        "CFO",
        "IPO",
        "EPS",
        "AI",
        "US",
        "Q1",
        "Q2",
        "Q3",
        "Q4",
        "FY",
        "GDP",
        "CPI",
    }
)

_TICKER_RE = re.compile(r"\b[A-Z]{1,5}\b")


def market_kpi_signals(market: dict[str, Any]) -> list[str]:
    """Return the KPI keywords present in a normalized market's text."""
    blob = _market_text(market).lower()
    return [kw for kw in KPI_KEYWORDS if kw in blob]


def is_kpi_market(market: dict[str, Any]) -> bool:
    return bool(market_kpi_signals(market))


def filter_kpi_markets(markets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only KPI/company markets, annotating each with its matched signals."""
    kept: list[dict[str, Any]] = []
    for market in markets:
        signals = market_kpi_signals(market)
        if not signals:
            continue
        annotated = dict(market)
        annotated["kpi_signals"] = signals
        kept.append(annotated)
    return kept


def extract_symbols_from_text(text: str) -> list[str]:
    """Best-effort ticker resolution from free text (no LLM).

    Prefers the curated alias map (company name -> ticker); falls back to
    uppercase ticker-shaped tokens minus a small stopword list.
    """
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


def resolve_symbols(
    explicit: list[str],
    *,
    query: str = "",
    markets: list[dict[str, Any]] | None = None,
) -> list[str]:
    """Explicit symbols win; otherwise infer from the query, then market text."""
    if explicit:
        return _dedupe_upper(explicit)

    inferred = extract_symbols_from_text(query)
    if inferred:
        return _dedupe_upper(inferred)

    market_blob = " ".join(_market_text(market) for market in (markets or []))
    return _dedupe_upper(extract_symbols_from_text(market_blob))


def parse_symbols(value: str | list[str] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        parts = re.split(r"[,\s]+", value)
    else:
        parts = list(value)
    return _dedupe_upper(parts)


def discover_company_kpis(
    *,
    query: str = "",
    symbols: str | list[str] | None = None,
    status: str = "open",
    max_markets: int = 60,
    kpi_only: bool = True,
    lookback_days: int = 120,
    max_fundamental_items: int = 5,
    include_fundamentals: bool = True,
) -> dict[str, Any]:
    """Orchestrate discovery + fundamentals. Network calls are best-effort."""
    # Imported lazily so the pure helpers above stay importable without the
    # provider stack (and so unit tests need no network).
    from kalshi_discovery.discovery import discover_kalshi

    clean_query = query.strip()
    explicit_symbols = parse_symbols(symbols)
    errors: list[dict[str, Any]] = []

    candidate_markets: list[dict[str, Any]] = []
    try:
        discovery = discover_kalshi(
            query=clean_query,
            status=status,
            max_markets=max(1, min(int(max_markets), 1000)),
        )
        candidate_markets = discovery.get("markets") or []
    except Exception as exc:  # pragma: no cover - network path
        errors.append({"stage": "kalshi_discovery", "error": str(exc)})

    kpi_markets = filter_kpi_markets(candidate_markets)
    returned_markets = kpi_markets if kpi_only else candidate_markets

    resolved = resolve_symbols(explicit_symbols, query=clean_query, markets=returned_markets)

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

    return {
        "tool": "company_fundamentals",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input": {
            "query": clean_query,
            "symbols": explicit_symbols,
            "status": status,
            "max_markets": int(max_markets),
            "kpi_only": kpi_only,
            "include_fundamentals": include_fundamentals,
        },
        "resolved_symbols": resolved,
        "symbols_were_inferred": not bool(explicit_symbols) and bool(resolved),
        "kpi_markets": [_slim_market(m) for m in returned_markets],
        "fundamentals": fundamentals,
        "counts": {
            "candidate_markets": len(candidate_markets),
            "kpi_markets": len(kpi_markets),
            "returned_markets": len(returned_markets),
            "resolved_symbols": len(resolved),
        },
        "errors": errors,
        "notes": (
            "Deterministic evidence only. Ticker resolution is best-effort when "
            "--symbols is omitted; verify resolved_symbols before relying on the "
            "fundamentals. The probability forecast is done by the agent, not here."
        ),
    }


def _market_text(market: dict[str, Any]) -> str:
    return " ".join(
        str(market.get(field) or "")
        for field in ("question", "description", "rules", "title")
    )


def _slim_market(market: dict[str, Any]) -> dict[str, Any]:
    return {
        "ticker": market.get("ticker"),
        "event_ticker": market.get("event_ticker"),
        "series_ticker": market.get("series_ticker"),
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

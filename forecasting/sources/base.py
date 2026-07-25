"""Source abstraction for company-KPI market venues.

Each venue (Kalshi, Polymarket, ...) implements `Source` and emits the SAME
unified row schema, so everything downstream — grouping into metric ladders,
selection, forecasting, the portfolio and the dashboard — stays source-agnostic.
This is the seam the Polymarket scope is built around: all venue-specific quirks
(question phrasing, bucket-vs-threshold shapes, URL formats, settlement) live
inside a Source and never leak past `discover()`.

SPIKE STATUS: `discover()` is implemented for Kalshi (local) and Polymarket
(live Gamma). `settle()` is declared but intentionally left for the MVP.
"""

from __future__ import annotations

from typing import Any, Protocol, TypedDict, runtime_checkable


class UnifiedRow(TypedDict, total=False):
    """One threshold/binary contract, normalized across venues.

    A metric is a *ladder* of these rows sharing (company, metric, period).
    `threshold` is the numeric ">= t" level this contract pays out above; it is
    None for a plain binary market (outcome_kind == "binary").
    """

    source: str            # "kalshi" | "polymarket"
    company: str           # display company, KPI/ticker suffix stripped
    metric: str            # best-effort metric label (fuzzy across venues)
    period: str            # "Q2 2026", "FY 2026", "2026", or "" if unknown
    question: str          # original venue question text
    threshold: float | None  # ">= threshold" value this contract resolves on
    yes_mid: float | None    # P(Yes), 0..1
    volume: float          # venue-native volume (contracts for Kalshi, USD for PM)
    close_time: str        # ISO8601
    ticker: str            # kalshi ticker | polymarket conditionId (stable join id)
    market_url: str        # live deep link to the venue market/event
    outcome_kind: str      # "threshold" | "bucket_cdf" | "binary"


@runtime_checkable
class Source(Protocol):
    """A venue that can enumerate its open company-KPI contracts."""

    name: str

    def discover(self) -> list[UnifiedRow]:
        """Return every open company-KPI contract as unified rows."""
        ...

    # ---- MVP, not spike -------------------------------------------------
    # def settle(self, positions: list[dict[str, Any]]) -> int:
    #     """Mark venue-resolved positions; return count newly settled."""
    #     ...


def normalize_company(name: str) -> str:
    """Loose company key for cross-source joins: drop ticker suffix, KPI tag,
    punctuation and case. 'Starbucks (SBUX)' and 'Starbucks KPI' -> 'starbucks'.
    Intentionally lossy — the scope flags cross-venue metric aliasing as the
    fuzzy part to harden in the MVP."""
    import re
    n = re.sub(r"\(([A-Z]{1,6})\)", "", name or "")      # drop (TICKER)
    n = re.sub(r"\bKPI\b", "", n, flags=re.I)
    n = re.sub(r"[^a-z0-9 ]", "", n.lower()).strip()
    return re.sub(r"\s+", " ", n)

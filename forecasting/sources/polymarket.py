"""Polymarket (Gamma API) company-KPI source.

Discovers open company-report / KPI *events* and normalizes each into the
unified ladder schema. Three market shapes are handled (see docs/polymarket_scope.md):

  A. cumulative threshold ladder  ("... revenue be above __?")   -> emitted as-is
  B. exclusive range buckets      ("<300k", "300k-325k", "475k+")-> bucket -> CDF
  C. single binary                ("... beat quarterly earnings?")-> one row

Decision (locked in): buckets are converted to a cumulative CDF so the whole
ladder looks like a monotone ">= t" set to everything downstream — one code path.
"""

from __future__ import annotations

import re
from typing import Any

from market_lookup.providers.common import get_json, parse_jsonish_list, probability
from forecasting.sources.base import UnifiedRow

GAMMA = "https://gamma-api.polymarket.com"

# Curated discovery terms — Polymarket has no "KPI category", so we search.
KPI_TERMS = [
    "quarterly earnings", "quarterly revenue", "revenue", "deliveries",
    "subscribers", "vehicles delivered", "units sold", "active users",
    "same store sales", "gross merchandise", "iphone", "azure", "cloud revenue",
]

SCALE = {"k": 1e3, "m": 1e6, "b": 1e9, "bn": 1e9, "t": 1e12,
         "thousand": 1e3, "million": 1e6, "billion": 1e9, "trillion": 1e12}

_MONEY = re.compile(r"\$?([\d,.]+)\s*(k|m|b|bn|t|thousand|million|billion|trillion)?\b", re.I)
_PERIOD = re.compile(r"\b(Q[1-4])\s*(\d{4})?\b|\bFY\s*(\d{4})\b|\b(20\d{2})\b", re.I)
_COMPANY = re.compile(r"^(?:will\s+|how many\s+|how much\s+)?([A-Za-z][\w&.\-' ]+?)\s*(?:\(([A-Z]{1,6})\))?\s+"
                      r"(?:q[1-4]|beat|deliver|report|revenue|to|will|\?)", re.I)


def _num(tok: str, scale: str | None) -> float | None:
    try:
        return float(tok.replace(",", "")) * SCALE.get((scale or "").lower(), 1.0)
    except (TypeError, ValueError):
        return None


def _period(text: str) -> str:
    m = _PERIOD.search(text or "")
    if not m:
        return ""
    if m.group(1):
        return f"{m.group(1).upper()} {m.group(2) or ''}".strip()
    if m.group(3):
        return f"FY {m.group(3)}"
    return m.group(4) or ""


def _company(title: str) -> str:
    m = _COMPANY.match(title or "")
    if m:
        return re.sub(r"\s+", " ", m.group(1).strip())
    # fallback: leading capitalized words
    return re.sub(r"\s+", " ", re.split(r"\bQ[1-4]\b|\?", title or "")[0].strip())


def _yes_price(mkt: dict[str, Any]) -> float | None:
    """Best available P(Yes) for a binary sub-market."""
    bid, ask = probability(mkt.get("bestBid")), probability(mkt.get("bestAsk"))
    if bid is not None and ask is not None:
        return round((bid + ask) / 2, 4)
    prices = parse_jsonish_list(mkt.get("outcomePrices"))
    return probability(prices[0]) if prices else (bid if bid is not None else ask)


def _bucket_range(label: str) -> tuple[float | None, float | None] | None:
    """Parse a bucket label into [lo, hi). '<300k'->(None,300k), '475k+'->(475k,None),
    '300k-325k'->(300k,325k). Returns None if not a recognizable bucket."""
    s = (label or "").strip().replace("–", "-").replace("—", "-")
    if s.startswith("<") or s.lower().startswith("under") or s.lower().startswith("less"):
        m = _MONEY.search(s)
        return (None, _num(m.group(1), m.group(2))) if m else None
    if s.endswith("+") or s.lower().endswith("or more") or s.lower().startswith("over"):
        m = _MONEY.search(s)
        return (_num(m.group(1), m.group(2)), None) if m else None
    nums = _MONEY.findall(s)
    if len(nums) >= 2:
        return (_num(nums[0][0], nums[0][1]), _num(nums[1][0], nums[1][1]))
    return None


def _classify(markets: list[dict[str, Any]]) -> str:
    if len(markets) <= 1:
        return "binary"
    labels = [m.get("groupItemTitle") or "" for m in markets]
    if sum(1 for l in labels if _bucket_range(l)) >= max(2, len(labels) - 1):
        return "bucket_cdf"
    return "threshold"


def _bucket_to_cdf(event: dict, markets: list[dict[str, Any]]) -> list[UnifiedRow]:
    """Convert exclusive range buckets into cumulative '>= edge' rows.
    P(value >= edge) = sum of yes-price over buckets whose lower edge >= edge."""
    parsed = []
    for m in markets:
        rng = _bucket_range(m.get("groupItemTitle") or "")
        p = _yes_price(m)
        if rng is None or p is None:
            continue
        parsed.append((rng[0], rng[1], p))  # (lo, hi, yes_price)
    if not parsed:
        return []
    total = sum(p for _, _, p in parsed)
    if not (0.85 <= total <= 1.15):          # buckets must partition probability ~1
        return []                            # malformed set -> skip (logged by caller)
    # candidate thresholds = finite lower edges
    edges = sorted({lo for lo, _, _ in parsed if lo is not None})
    rows: list[UnifiedRow] = []
    for b in edges:
        cum = sum(p for lo, _, p in parsed if lo is not None and lo >= b)
        rows.append(_row(event, threshold=b, yes_mid=round(cum, 4),
                         kind="bucket_cdf",
                         question=f"P(value >= {b:g}) [from buckets]"))
    return rows


def _threshold_rows(event: dict, markets: list[dict[str, Any]]) -> list[UnifiedRow]:
    rows: list[UnifiedRow] = []
    for m in markets:
        label = m.get("groupItemTitle") or m.get("question") or ""
        mm = _MONEY.search(label.replace("above", "").replace("over", ""))
        thr = _num(mm.group(1), mm.group(2)) if mm else None
        rows.append(_row(event, threshold=thr, yes_mid=_yes_price(m),
                         kind="threshold", question=m.get("question") or label,
                         ticker=m.get("conditionId")))
    return rows


def _binary_row(event: dict, markets: list[dict[str, Any]]) -> list[UnifiedRow]:
    m = markets[0] if markets else {}
    return [_row(event, threshold=None, yes_mid=_yes_price(m), kind="binary",
                 question=m.get("question") or event.get("title"),
                 ticker=m.get("conditionId"))]


def _row(event: dict, *, threshold, yes_mid, kind, question, ticker=None) -> UnifiedRow:
    title = event.get("title") or ""
    return {
        "source": "polymarket",
        "company": _company(title),
        "metric": _metric(title),
        "period": _period(title) or _period(event.get("slug") or ""),
        "question": question or "",
        "threshold": threshold,
        "yes_mid": yes_mid,
        "volume": float(event.get("volume") or 0.0),
        "close_time": event.get("endDate") or "",
        "ticker": ticker or event.get("id") or event.get("slug") or "",
        "market_url": f"https://polymarket.com/event/{event.get('slug')}",
        "outcome_kind": kind,
    }


def _metric(title: str) -> str:
    """Rough metric label: strip company/period scaffolding. Fuzzy by design."""
    t = re.sub(_COMPANY, "", title or "", count=1)
    t = re.sub(r"\(([A-Z]{1,6})\)|\bQ[1-4]\b|\b20\d{2}\b|\?|be above __|beat", " ", t)
    t = re.sub(r"\s+", " ", t).strip(" -:")
    return t or (title or "").strip()


def normalize_event(event: dict[str, Any]) -> tuple[list[UnifiedRow], str]:
    """Return (rows, shape). Empty rows means unusable/malformed for v1."""
    markets = [m for m in (event.get("markets") or []) if isinstance(m, dict)]
    shape = _classify(markets)
    if shape == "binary":
        return _binary_row(event, markets), shape
    if shape == "bucket_cdf":
        return _bucket_to_cdf(event, markets), shape
    return _threshold_rows(event, markets), shape


class PolymarketSource:
    name = "polymarket"

    def __init__(self, *, terms: list[str] | None = None, per_term: int = 15,
                 include_closed: bool = False):
        self.terms = terms or KPI_TERMS
        self.per_term = per_term
        self.include_closed = include_closed
        self.debug: dict[str, Any] = {"events": 0, "skipped_malformed": 0, "by_shape": {}}

    def _discover_events(self) -> list[dict]:
        seen: dict[str, dict] = {}
        for q in self.terms:
            try:
                payload = get_json(f"{GAMMA}/public-search", {"q": q, "limit": self.per_term})
            except Exception:
                continue
            for e in payload.get("events") or []:
                if not isinstance(e, dict) or not e.get("markets"):
                    continue
                if not self.include_closed and e.get("closed"):
                    continue
                title = e.get("title") or ""
                # exclude mention / earnings-call markets (categorical, multi-true;
                # a different resolution model — out of KPI scope, see scope doc).
                if re.search(r"\bsay(s)?\b.*\b(earnings|call)\b|earnings call", title, re.I):
                    continue
                # Require a company ticker AND a financial-KPI signal. This is the
                # production filter: it drops non-company noise (creators, product
                # releases, off-topic) that a looser ticker-OR-keyword filter let in.
                has_ticker = bool(re.search(r"\([A-Z]{1,6}\)", title))
                has_kpi = bool(re.search(
                    r"\b(earnings|revenue|deliver(y|ies|ed)?|subscribers?|sales|"
                    r"units|users|gross|volume|eps|guidance|shipments?|bookings?|"
                    r"margin|growth|accounts?|stores?)\b", title, re.I))
                if not (has_ticker and has_kpi):
                    continue
                seen[str(e.get("id") or e.get("slug"))] = e
        return list(seen.values())

    def discover(self) -> list[UnifiedRow]:
        rows: list[UnifiedRow] = []
        for e in self._discover_events():
            self.debug["events"] += 1
            r, shape = normalize_event(e)
            self.debug["by_shape"][shape] = self.debug["by_shape"].get(shape, 0) + 1
            if not r:
                self.debug["skipped_malformed"] += 1
                continue
            rows.extend(r)
        return rows

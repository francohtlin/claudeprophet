"""Pull open Kalshi Climate/Weather markets -> data/weather_open.jsonl.

These are the weather markets Wealthsimple Predict can offer in Canada (the
CIRO-authorized 'climate' category, contracts settling >= 30 days out). We keep
only numeric threshold ladders that carry a live price, emitted in the same
unified row schema the dashboard's ladder machinery already understands.
"""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backtest.kalshi import list_series, list_markets

OUT = Path(__file__).resolve().parents[1] / "data" / "weather_open.jsonl"
MIN_SETTLE_DAYS = 30  # Wealthsimple/CIRO: contracts with a >=30-day settlement period
_SEG = re.compile(r"(\d{2})([A-Z]{3})(\d{2})?")


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _mid(m):
    """Yes mid-price in [0,1] from dollar or cent fields."""
    yb, ya = _num(m.get("yes_bid_dollars")), _num(m.get("yes_ask_dollars"))
    if yb is not None and ya is not None:
        return round((yb + ya) / 2, 3)
    yb, ya = _num(m.get("yes_bid")), _num(m.get("yes_ask"))
    if yb is not None and ya is not None:
        return round((yb + ya) / 200, 3)  # cents -> prob
    return None


def _market_url(ticker: str) -> str:
    if not ticker or ticker.count("-") < 2:
        return ""
    return f"https://kalshi.com/markets/{ticker.split('-')[0]}/{ticker.rsplit('-', 1)[0]}"


def _period(ticker: str) -> str:
    """Humanize the period segment of a Kalshi ticker: KXRAINNYC-26AUG-3 -> 'Aug 2026'."""
    parts = (ticker or "").split("-")
    if len(parts) < 3:
        return ""
    m = _SEG.match(parts[1])
    if not m:
        return parts[1]
    yy, mon, dd = m.groups()
    return f"{mon.title()} {dd + ' ' if dd else ''}20{yy}".strip()


def _variant(ticker: str) -> str:
    """The event-ticker suffix after the date (e.g. 'CPACTOT', 'EPACTOT') that
    separates distinct ladders sharing a series+period (different basins/regions).
    Empty for single-ladder series (rain, temperature)."""
    parts = (ticker or "").split("-")
    if len(parts) < 3:
        return ""
    m = _SEG.match(parts[1])
    return parts[1][m.end():] if m else ""


def main() -> int:
    cutoff = datetime.now(timezone.utc) + timedelta(days=MIN_SETTLE_DAYS)
    rows = []
    n_series = 0
    for s in list_series("Climate and Weather"):
        st = s.get("ticker")
        if not st:
            continue
        n_series += 1
        ms = []
        for _ in range(3):
            try:
                ms = list_markets(st, status="open", max_markets=200)
                break
            except Exception:
                time.sleep(0.4)
        for m in ms:
            strike = _num(m.get("floor_strike"))
            if strike is None or m.get("strike_type") not in ("greater", "greater_or_equal"):
                continue  # numeric ">=" ladders only (skip categorical/custom)
            mid = _mid(m)
            if mid is None:
                continue  # needs a live price to have an edge
            close = m.get("close_time") or m.get("expiration_time") or ""
            try:
                if datetime.fromisoformat(close.replace("Z", "+00:00")) < cutoff:
                    continue  # settles too soon for Wealthsimple's >=30-day rule
            except Exception:
                continue
            # Group by series + period (constant across a ladder's rungs, unlike
            # per-rung titles that embed the strike).
            rows.append({
                "source": "kalshi-weather", "company": s.get("title", st),
                "metric": _variant(m.get("ticker", "")), "period": _period(m.get("ticker", "")),
                "question": m.get("title", ""),
                "outcome_kind": "threshold", "threshold": strike, "yes_mid": mid,
                "volume": _num(m.get("volume")) or 0.0, "close_time": close,
                "ticker": m.get("ticker"), "series_ticker": st,
                "market_url": _market_url(m.get("ticker", "")),
            })
    rows = [r for r in rows if r["ticker"] and r["question"]]
    rows.sort(key=lambda r: (r.get("close_time") or "", r["company"]))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("".join(json.dumps(r) + "\n" for r in rows))
    metrics = len({(r["company"], r["metric"], r["period"]) for r in rows})
    print(f"wrote {len(rows)} open weather contracts across {metrics} metrics "
          f"({n_series} series scanned, >= {MIN_SETTLE_DAYS}-day settle) -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

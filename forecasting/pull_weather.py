"""Pull open Kalshi Climate/Weather markets -> data/weather_open.jsonl.

These are the weather markets Wealthsimple Predict can offer in Canada (the
CIRO-authorized 'climate' category, contracts settling >= 30 days out). We keep
only numeric threshold ladders that carry a live price, emitted in the same
unified row schema the dashboard's ladder machinery already understands.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backtest.kalshi import list_series, list_markets
from forecasting.weather_tracks import cfg

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


def _is_daily(ticker: str) -> bool:
    """True if the ticker period targets a specific day (e.g. 26JUL30) rather than
    a whole month (26JUL) — i.e. a same-day daily-temperature market."""
    parts = (ticker or "").split("-")
    if len(parts) < 3:
        return False
    m = _SEG.match(parts[1])
    return bool(m and m.group(3))  # has a day component


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--track", default="wealthsimple", choices=["wealthsimple", "nearterm"])
    c = cfg(ap.parse_args().track)
    now = datetime.now(timezone.utc)
    lo = now + timedelta(days=c["min_settle_days"])
    hi = now + timedelta(days=c["max_settle_days"]) if c["max_settle_days"] else None
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
            if c["monthly_only"] and _is_daily(m.get("ticker", "")):
                continue  # skip same-day daily-temp markets for the near-term totals track
            mid = _mid(m)
            if mid is None:
                continue  # needs a live price to have an edge
            close = m.get("close_time") or m.get("expiration_time") or ""
            try:
                ct = datetime.fromisoformat(close.replace("Z", "+00:00"))
                if ct < lo or (hi and ct > hi):
                    continue  # outside this track's settlement window
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
    out = c["open"]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(r) + "\n" for r in rows))
    metrics = len({(r["company"], r["metric"], r["period"]) for r in rows})
    win = f">= {c['min_settle_days']}d" + (f", <= {c['max_settle_days']}d" if c["max_settle_days"] else "")
    print(f"wrote {len(rows)} open weather contracts across {metrics} metrics "
          f"({n_series} series scanned, {c['label']}: {win} settle) -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

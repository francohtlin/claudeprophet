"""Fetch pre-release market prices for resolved KPI contracts (backtest entries).

For each resolved contract, records the last trade price at least SNAP_HOURS
before the market closed — i.e., the crowd's genuine pre-release probability.
Output: data/forecasts/backtest_prices.json  {ticker: {"pre_yes": p, "trade_ts": iso}}
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backtest.kalshi import get_json, KALSHI_BASE
from forecasting.backtest_blind import build_groups

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "forecasts" / "backtest_prices.json"
SNAP_HOURS = 24


def ts(s: str) -> int | None:
    try:
        return int(datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp())
    except (ValueError, TypeError):
        return None


def yes_price(t: dict) -> float | None:
    for k in ("yes_price_dollars", "yes_price"):
        v = t.get(k)
        if v not in (None, ""):
            try:
                f = float(v)
                return f if f <= 1 else f / 100.0
            except ValueError:
                pass
    return None


def trades_for(ticker: str) -> list[dict]:
    out: list[dict] = []
    for base in (f"{KALSHI_BASE}/markets/trades", f"{KALSHI_BASE}/historical/trades"):
        cursor = ""
        for _ in range(8):
            p = {"ticker": ticker, "limit": 1000}
            if cursor:
                p["cursor"] = cursor
            try:
                r = get_json(base, p, timeout=20.0)
            except Exception:
                break
            batch = r.get("trades") or []
            out += batch
            cursor = str(r.get("cursor") or "")
            if not cursor or not batch:
                break
        if out:
            break
    return out


def main() -> int:
    close_by_ticker: dict[str, int] = {}
    rows = [json.loads(l) for l in (ROOT / "data" / "company_kpi_full.jsonl").open()]
    for r in rows:
        c = ts(r.get("close_time"))
        if r.get("ticker") and c:
            close_by_ticker[r["ticker"]] = c

    prices = json.loads(OUT.read_text()) if OUT.exists() else {}
    todo = []
    for g in build_groups():
        for c in g["contracts"]:
            if c["ticker"] not in prices:
                todo.append(c["ticker"])
    print(f"{len(todo)} contracts to price", flush=True)

    for i, tk in enumerate(todo, 1):
        close = close_by_ticker.get(tk)
        if not close:
            continue
        cutoff = close - SNAP_HOURS * 3600
        best = None  # latest trade at/before cutoff
        for t in trades_for(tk):
            tt = ts(t.get("created_time"))
            p = yes_price(t)
            if tt is None or p is None or tt > cutoff:
                continue
            if best is None or tt > best[0]:
                best = (tt, p)
        if best:
            prices[tk] = {"pre_yes": best[1],
                          "trade_ts": datetime.fromtimestamp(best[0], timezone.utc).isoformat()}
        if i % 25 == 0:
            OUT.write_text(json.dumps(prices, indent=1) + "\n")
            print(f"  {i}/{len(todo)} priced ({len(prices)} with pre-release trades)", flush=True)
    OUT.write_text(json.dumps(prices, indent=1) + "\n")
    print(f"DONE: {len(prices)} contracts have a pre-release price -> {OUT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

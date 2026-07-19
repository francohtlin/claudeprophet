from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from company_fundamentals.fundamentals import discover_company_kpis
from local_env import load_local_env


def main(argv: list[str] | None = None) -> int:
    load_local_env()
    parser = argparse.ArgumentParser(prog="company_fundamentals")
    parser.add_argument("--query", default="", help="Company name / KPI to match against Kalshi company series.")
    parser.add_argument("--symbols", default="", help="Comma/space-separated tickers, e.g. NVDA,AAPL. Preferred over inference.")
    parser.add_argument("--categories", default="Companies,Financials", help="Kalshi series categories to search.")
    parser.add_argument("--status", default="open", help="Kalshi market status: open, closed, settled, or all.")
    parser.add_argument("--max-series", type=int, default=25, help="Cap on matched series to fetch markets for.")
    parser.add_argument("--max-markets-per-series", type=int, default=100)
    parser.add_argument("--no-fundamentals", action="store_true", help="Skip the finance_lookup fundamentals pull.")
    parser.add_argument("--lookback-days", type=int, default=120)
    parser.add_argument("--max-fundamental-items", type=int, default=5)
    parser.add_argument("--output", default=None, help="Optional path to also write JSON output.")
    args = parser.parse_args(argv)

    result = discover_company_kpis(
        query=args.query,
        symbols=args.symbols,
        categories=args.categories,
        status=args.status,
        max_series=args.max_series,
        max_markets_per_series=args.max_markets_per_series,
        lookback_days=args.lookback_days,
        max_fundamental_items=args.max_fundamental_items,
        include_fundamentals=not args.no_fundamentals,
    )

    text = json.dumps(result, indent=2, sort_keys=False, ensure_ascii=False)
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")
    sys.stdout.write(text)
    sys.stdout.write("\n")
    return 0

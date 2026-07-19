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
    parser.add_argument("--query", default="", help="Text filter, e.g. company name or KPI, applied to Kalshi markets.")
    parser.add_argument("--symbols", default="", help="Comma/space-separated tickers, e.g. NVDA,AAPL. Preferred over inference.")
    parser.add_argument("--status", default="open", help="Kalshi market status: open, closed, settled, or all.")
    parser.add_argument("--max-markets", type=int, default=60, help="Candidate market cap before KPI filtering.")
    parser.add_argument("--all-markets", action="store_true", help="Return all candidate markets, not just KPI matches.")
    parser.add_argument("--no-fundamentals", action="store_true", help="Skip the finance_lookup fundamentals pull.")
    parser.add_argument("--lookback-days", type=int, default=120)
    parser.add_argument("--max-fundamental-items", type=int, default=5)
    parser.add_argument("--output", default=None, help="Optional path to also write JSON output.")
    args = parser.parse_args(argv)

    result = discover_company_kpis(
        query=args.query,
        symbols=args.symbols,
        status=args.status,
        max_markets=args.max_markets,
        kpi_only=not args.all_markets,
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

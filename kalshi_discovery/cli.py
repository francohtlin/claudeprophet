from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from kalshi_discovery.discovery import discover_kalshi


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kalshi_discovery")
    parser.add_argument("--query", default="", help="Optional text filter applied after retrieval.")
    parser.add_argument("--status", default="open", help="open, closed, settled, determined, initialized, or all.")
    parser.add_argument("--series-ticker", default=None)
    parser.add_argument("--event-ticker", default=None)
    parser.add_argument("--category", default=None, help="Optional series category discovery hint.")
    parser.add_argument("--max-pages", type=int, default=10, help="Safety cap. Use 0 for no cap.")
    parser.add_argument(
        "--max-series",
        type=int,
        default=10,
        help="Safety cap for category-derived series. Use 0 for no cap.",
    )
    parser.add_argument("--limit-per-page", type=int, default=1000)
    parser.add_argument("--max-markets", type=int, default=250, help="Output cap after retrieval/filtering.")
    parser.add_argument("--include-orderbook", action="store_true", help="Fetch orderbook depth for returned markets.")
    parser.add_argument("--orderbook-depth", type=int, default=10)
    parser.add_argument("--include-raw", action="store_true")
    parser.add_argument("--output", default=None, help="Optional path to write JSON output.")
    args = parser.parse_args(argv)

    result = discover_kalshi(
        query=args.query,
        status=args.status,
        series_ticker=args.series_ticker,
        event_ticker=args.event_ticker,
        category=args.category,
        max_pages=args.max_pages,
        max_series=args.max_series,
        limit_per_page=args.limit_per_page,
        max_markets=args.max_markets,
        include_orderbook=args.include_orderbook,
        orderbook_depth=args.orderbook_depth,
        include_raw=args.include_raw,
    )

    text = json.dumps(result, indent=2, sort_keys=False, ensure_ascii=False)
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")
    sys.stdout.write(text)
    sys.stdout.write("\n")
    return 0

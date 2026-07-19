from __future__ import annotations

import argparse
import json
import sys

from finance_lookup.lookup import lookup_finance, self_test
from local_env import load_local_env


def main(argv: list[str] | None = None) -> int:
    load_local_env()
    parser = argparse.ArgumentParser(prog="finance_lookup")
    parser.add_argument("--query", default="", help="Natural-language finance question or context.")
    parser.add_argument("--symbols", default="", help="Comma-separated tickers/symbols, e.g. NVDA,BTC.")
    parser.add_argument(
        "--asset-type",
        default="auto",
        choices=["auto", "equity", "crypto", "macro", "filings"],
        help="Hint for routing symbols to providers.",
    )
    parser.add_argument(
        "--data-needed",
        default="price,history,news",
        help="Comma-separated data types: price,history,news,macro,filings.",
    )
    parser.add_argument("--lookback-days", type=int, default=30)
    parser.add_argument("--max-items", type=int, default=5)
    parser.add_argument(
        "--with-debug",
        action="store_true",
        help="Include provider status and non-agent debugging fields.",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run provider install/config/live checks instead of a normal lookup.",
    )
    args = parser.parse_args(argv)

    if args.self_test:
        result = self_test()
    else:
        result = lookup_finance(
            query=args.query,
            symbols=args.symbols,
            asset_type=args.asset_type,
            data_needed=args.data_needed,
            lookback_days=args.lookback_days,
            max_items=args.max_items,
            include_debug=args.with_debug,
        )
    json.dump(result, sys.stdout, indent=2, sort_keys=False, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0

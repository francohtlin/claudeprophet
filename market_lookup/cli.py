from __future__ import annotations

import argparse
import json
import sys

from market_lookup.lookup import lookup_markets


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="market_lookup")
    parser.add_argument("--text", required=True, help="Natural-language event/query text.")
    parser.add_argument("--category", default=None, help="Optional event category. Not used for search.")
    parser.add_argument(
        "--ideal-close-time",
        default=None,
        help="Optional ideal close time. Not used for search.",
    )
    parser.add_argument("--max-markets", type=int, default=10)
    parser.add_argument(
        "--include-history",
        action="store_true",
        help="Attach public Kalshi trade/candlestick history to matched Kalshi markets.",
    )
    parser.add_argument("--history-lookback-days", type=int, default=7)
    parser.add_argument("--history-trade-limit", type=int, default=50)
    parser.add_argument("--history-candle-limit", type=int, default=48)
    parser.add_argument(
        "--with-debug",
        action="store_true",
        help="Include provider/debug traces. Do not feed this mode directly to the forecasting agent.",
    )
    args = parser.parse_args(argv)

    result = lookup_markets(
        text=args.text,
        category=args.category,
        ideal_close_time=args.ideal_close_time,
        max_markets=args.max_markets,
        include_history=args.include_history,
        history_lookback_days=args.history_lookback_days,
        history_trade_limit=args.history_trade_limit,
        history_candle_limit=args.history_candle_limit,
        include_debug=args.with_debug,
    )
    json.dump(result, sys.stdout, indent=2, sort_keys=False, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0

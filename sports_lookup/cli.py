from __future__ import annotations

import argparse
import json
import sys

from local_env import load_local_env
from sports_lookup.lookup import lookup_sports, self_test


def main(argv: list[str] | None = None) -> int:
    load_local_env()
    parser = argparse.ArgumentParser(prog="sports_lookup")
    parser.add_argument("--query", default="", help="Natural-language sports query.")
    parser.add_argument("--sport", default="auto", help="auto, nfl, nba, mlb, nhl, soccer, tennis.")
    parser.add_argument("--date", default=None, help="Start date YYYY-MM-DD. Defaults to today UTC.")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--max-events", type=int, default=10)
    parser.add_argument("--include-odds", action="store_true", help="Use THE_ODDS_API_KEY if configured.")
    parser.add_argument("--with-debug", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    result = (
        self_test()
        if args.self_test
        else lookup_sports(
            query=args.query,
            sport=args.sport,
            date=args.date,
            days=args.days,
            max_events=args.max_events,
            include_odds=args.include_odds,
            include_debug=args.with_debug,
        )
    )
    json.dump(result, sys.stdout, indent=2, sort_keys=False, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0

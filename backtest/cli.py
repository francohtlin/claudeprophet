from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from backtest.data import build_cases
from backtest.forecasters import build as build_forecaster
from backtest.report import render_cases, render_summary
from backtest.runner import run_backtest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="backtest", description="Walk-forward backtest of Kalshi forecasts.")
    parser.add_argument("--series", required=True, help="Comma-separated Kalshi series tickers, e.g. KXCPIYOY,KXPAYROLLS.")
    parser.add_argument("--forecasters", default="base_rate,market", help="Comma-separated: base_rate,market,claudeprophet.")
    parser.add_argument("--max-per-series", type=int, default=200)
    parser.add_argument("--snapshot-days", type=int, default=3, help="Days before close to snapshot the market price (0 to skip).")
    parser.add_argument("--limit", type=int, default=0, help="Cap total cases after building (0 = no cap).")
    parser.add_argument("--endpoint", default="http://127.0.0.1:8080", help="ClaudeProphet base URL for the agent forecaster.")
    parser.add_argument("--timeout", type=float, default=600.0, help="Per-request timeout for the agent forecaster.")
    parser.add_argument("--output", default=None, help="Write the full JSON result (metrics + records) here.")
    parser.add_argument("--records-jsonl", default=None, help="Write one JSON record per case here.")
    parser.add_argument("--show-cases", type=int, default=20, help="How many per-case rows to print (0 to hide).")
    args = parser.parse_args(argv)

    series = [s.strip() for s in args.series.split(",") if s.strip()]
    snapshot_days = args.snapshot_days if args.snapshot_days > 0 else None

    cases = build_cases(series, max_per_series=args.max_per_series, snapshot_days=snapshot_days)
    if args.limit > 0:
        cases = cases[: args.limit]
    if not cases:
        print("No resolved cases found for the given series.", file=sys.stderr)
        return 1

    forecasters = [
        build_forecaster(name.strip(), endpoint=args.endpoint, timeout=args.timeout)
        for name in args.forecasters.split(",")
        if name.strip()
    ]

    result = run_backtest(cases, forecasters)

    print(render_summary(result))
    if args.show_cases:
        print(render_cases(result, limit=args.show_cases))

    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    if args.records_jsonl:
        Path(args.records_jsonl).parent.mkdir(parents=True, exist_ok=True)
        with Path(args.records_jsonl).open("w", encoding="utf-8") as fh:
            for rec in result["records"]:
                fh.write(json.dumps(rec) + "\n")
    return 0

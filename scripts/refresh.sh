#!/usr/bin/env bash
# Daily refresh: pull fresh Kalshi prices, mark any settled positions, rebuild
# the dashboard, and push (which updates the GitHub Pages link). No LLM in this
# path — it's free and cannot hit rate limits. New forecasts are a separate,
# manual/weekly step (forecasting/select_kpi.py + forecast_kpi.py).
set -uo pipefail

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p logs
LOG="logs/refresh.log"

{
  echo "===== refresh $(date -u +%FT%TZ) ====="
  python3 forecasting/pull_kpi_markets.py || echo "WARN: pull failed"
  python3 forecasting/portfolio.py mark    || echo "WARN: mark failed"
  python3 dashboard/gen_dashboard.py       || echo "WARN: gen failed"

  # Scoped add — never touch anything but the dashboard's own outputs.
  git add docs/index.html \
          data/company_kpi_open.jsonl \
          data/portfolio.json \
          data/backtest_portfolio.json \
          data/forecasts/open_kpi_claudeprophet.jsonl \
          data/forecasts/resolved_scores.jsonl 2>/dev/null

  if git diff --cached --quiet; then
    echo "no changes"
  else
    git commit -q -m "Daily refresh $(date -u +%F)"
    git push 2>&1 | tail -1
    echo "committed + pushed"
  fi
  echo "===== done ====="
} >> "$LOG" 2>&1

tail -8 "$LOG"

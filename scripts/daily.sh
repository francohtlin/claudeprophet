#!/usr/bin/env bash
# One-command daily catch-up. Run it whenever you sit down — it does not care
# what day it is or how long since the last run:
#   prices -> forecast any newly-listed uncertain metrics -> open paper
#   positions -> score settlements -> rebuild dashboard -> push.
#
# Self-limiting: only metrics that are genuinely uncertain and not already
# forecasted get picked up, capped at MAX_FORECASTS per run (default 10), so a
# quiet day costs nothing and a busy one cannot run away. Each forecast is a
# `claude -p` child on your CLI subscription login — no API key, no per-token
# billing.
#
# Usage:
#   scripts/daily.sh                 # full cycle, commits and pushes
#   scripts/daily.sh --no-push       # everything local, commit but no push
#   MAX_FORECASTS=25 scripts/daily.sh
set -uo pipefail

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p logs
LOG="logs/daily.log"
MAX_FORECASTS="${MAX_FORECASTS:-10}"

PUSH=1
for a in "$@"; do
  [ "$a" = "--no-push" ] && PUSH=0
done

{
  echo "===== daily $(date -u +%FT%TZ) (cap ${MAX_FORECASTS}) ====="

  echo "-- 1/6 pull fresh Kalshi prices"
  python3 forecasting/pull_kpi_markets.py || echo "WARN: pull failed"

  echo "-- 2/6 select new uncertain metrics"
  python3 forecasting/select_kpi.py -n "$MAX_FORECASTS" || echo "WARN: select failed"

  n=$(python3 -c "import json;print(len(json.load(open('data/forecasts/_chosen.json'))))" 2>/dev/null || echo 0)
  echo "-- 3/6 forecast (${n} new metric(s))"
  if [ "$n" -gt 0 ]; then
    python3 forecasting/forecast_kpi.py || echo "WARN: forecast failed"
  else
    echo "nothing new to forecast - skipping"
  fi

  echo "-- 4/6 open paper positions"
  python3 forecasting/portfolio.py add || echo "WARN: add failed"

  echo "-- 5/6 score settlements"
  python3 forecasting/portfolio.py mark || echo "WARN: mark failed"

  echo "-- 6/6 rebuild dashboard"
  python3 dashboard/gen_dashboard.py || echo "WARN: gen failed"

  # Scoped add - never touch anything but this pipeline's own outputs.
  git add docs/index.html \
          data/company_kpi_open.jsonl \
          data/portfolio.json \
          data/forecasts/open_kpi_claudeprophet.jsonl \
          data/forecasts/resolved_scores.jsonl 2>/dev/null

  if git diff --cached --quiet; then
    echo "no changes"
  else
    git commit -q -m "Daily forecasts + scoring $(date -u +%F)"
    if [ "$PUSH" -eq 1 ]; then
      git push 2>&1 | tail -1
      echo "committed + pushed"
    else
      echo "committed (push skipped: --no-push)"
    fi
  fi
  echo "===== done ====="
} >> "$LOG" 2>&1

tail -30 "$LOG"

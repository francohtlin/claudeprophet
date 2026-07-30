#!/usr/bin/env bash
# One-command daily catch-up for BOTH venues (Kalshi + Polymarket). Run it
# whenever you sit down — it does not care what day it is or how long since the
# last run:
#   prices -> forecast any newly-listed uncertain metrics -> open paper
#   positions -> score settlements -> rebuild dashboard -> push.
#
# Self-limiting: only metrics that are genuinely uncertain and not already
# forecasted get picked up, capped at MAX_FORECASTS per venue per run (default
# 10), so a quiet day costs nothing and a busy one cannot run away. Each forecast
# is a `claude -p` child on your CLI subscription login — no API key, no
# per-token billing. (FutureSearch is intentionally NOT part of this cycle.)
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

count() { python3 -c "import json;print(len(json.load(open('$1'))))" 2>/dev/null || echo 0; }

{
  echo "===== daily $(date -u +%FT%TZ) (cap ${MAX_FORECASTS}/venue) ====="

  echo "== KALSHI =="
  echo "-- pull fresh prices"
  python3 forecasting/pull_kpi_markets.py || echo "WARN: kalshi pull failed"
  echo "-- select new uncertain metrics"
  python3 forecasting/select_kpi.py -n "$MAX_FORECASTS" || echo "WARN: kalshi select failed"
  n=$(count data/forecasts/_chosen.json)
  echo "-- forecast (${n} new)"
  if [ "$n" -gt 0 ]; then python3 forecasting/forecast_kpi.py || echo "WARN: kalshi forecast failed"; else echo "nothing new"; fi
  echo "-- open positions"; python3 forecasting/portfolio.py add  || echo "WARN: kalshi add failed"
  echo "-- score settlements"; python3 forecasting/portfolio.py mark || echo "WARN: kalshi mark failed"

  echo "== POLYMARKET EARNINGS =="
  echo "-- pull fresh prices"
  python3 forecasting/pull_polymarket_kpi.py || echo "WARN: pm pull failed"
  echo "-- select new uncertain metrics"
  python3 forecasting/select_pm.py -n "$MAX_FORECASTS" || echo "WARN: pm select failed"
  p=$(count data/forecasts/_chosen_pm.json)
  echo "-- forecast (${p} new)"
  if [ "$p" -gt 0 ]; then python3 forecasting/forecast_pm.py || echo "WARN: pm forecast failed"; else echo "nothing new"; fi
  echo "-- open positions"; python3 forecasting/pm_portfolio.py add  || echo "WARN: pm add failed"
  echo "-- score settlements"; python3 forecasting/pm_portfolio.py mark || echo "WARN: pm mark failed"

  echo "== POLYMARKET KPIS =="
  echo "-- pull fresh prices"
  python3 forecasting/pull_polymarket_kpi.py --track kpis || echo "WARN: pmkpi pull failed"
  echo "-- select new uncertain metrics"
  python3 forecasting/select_pm.py --track kpis -n "$MAX_FORECASTS" || echo "WARN: pmkpi select failed"
  pk=$(count data/forecasts/_chosen_pmkpi.json)
  echo "-- forecast (${pk} new)"
  if [ "$pk" -gt 0 ]; then python3 forecasting/forecast_pm.py --track kpis || echo "WARN: pmkpi forecast failed"; else echo "nothing new"; fi
  echo "-- open positions"; python3 forecasting/pm_portfolio.py --track kpis add  || echo "WARN: pmkpi add failed"
  echo "-- score settlements"; python3 forecasting/pm_portfolio.py --track kpis mark || echo "WARN: pmkpi mark failed"

  echo "== WEATHER (Kalshi climate, Wealthsimple-eligible) =="
  echo "-- pull fresh prices"
  python3 forecasting/pull_weather.py || echo "WARN: weather pull failed"
  echo "-- select new uncertain ladders"
  python3 forecasting/select_weather.py -n "$MAX_FORECASTS" || echo "WARN: weather select failed"
  w=$(count data/forecasts/_chosen_weather.json)
  echo "-- forecast (${w} new)"
  if [ "$w" -gt 0 ]; then python3 forecasting/forecast_weather.py || echo "WARN: weather forecast failed"; else echo "nothing new"; fi
  echo "-- open positions"; python3 forecasting/weather_portfolio.py add  || echo "WARN: weather add failed"
  echo "-- score settlements"; python3 forecasting/weather_portfolio.py mark || echo "WARN: weather mark failed"

  echo "== WEATHER NEAR-TERM (fast-resolving monthly totals) =="
  echo "-- pull fresh prices"
  python3 forecasting/pull_weather.py --track nearterm || echo "WARN: weather-nt pull failed"
  echo "-- select new uncertain ladders"
  python3 forecasting/select_weather.py --track nearterm -n "$MAX_FORECASTS" || echo "WARN: weather-nt select failed"
  wn=$(count data/forecasts/_chosen_weather_nt.json)
  echo "-- forecast (${wn} new)"
  if [ "$wn" -gt 0 ]; then python3 forecasting/forecast_weather.py --track nearterm || echo "WARN: weather-nt forecast failed"; else echo "nothing new"; fi
  echo "-- open positions"; python3 forecasting/weather_portfolio.py --track nearterm add  || echo "WARN: weather-nt add failed"
  echo "-- score settlements"; python3 forecasting/weather_portfolio.py --track nearterm mark || echo "WARN: weather-nt mark failed"

  echo "== rebuild dashboard =="
  python3 dashboard/gen_dashboard.py || echo "WARN: gen failed"

  # Scoped add - only this pipeline's own outputs, both venues. Never the
  # generator source or unrelated WIP (FutureSearch / RL live outside this add).
  git add docs/index.html \
          data/company_kpi_open.jsonl \
          data/portfolio.json \
          data/forecasts/open_kpi_claudeprophet.jsonl \
          data/forecasts/resolved_scores.jsonl \
          data/polymarket_kpi_open.jsonl \
          data/forecasts/open_pm_claudeprophet.jsonl \
          data/pm_portfolio.json \
          data/polymarket_kpis_open.jsonl \
          data/forecasts/open_pmkpi_claudeprophet.jsonl \
          data/pmkpi_portfolio.json \
          data/weather_open.jsonl \
          data/forecasts/open_weather_claudeprophet.jsonl \
          data/weather_portfolio.json \
          data/weather_nt_open.jsonl \
          data/forecasts/open_weather_nt_claudeprophet.jsonl \
          data/weather_nt_portfolio.json 2>/dev/null

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

tail -40 "$LOG"

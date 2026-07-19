# ClaudeProphet Forecast Sequence

This is the intended sequence for both interactive and API-triggered forecasts.

## Interactive

```text
human
  -> npm run claude:prophet -- <event or event.json>
  -> Claude starts with claude-opus-4-8 and the WebSearch tool
  -> Claude reads AGENTS.md
  -> Claude selects relevant skills
  -> Claude calls local CLIs and web search
  -> Claude writes scratch event/prediction JSON under tmp/
  -> Claude validates with submit_prediction
  -> Claude revises until valid
  -> Claude returns final JSON
```

## Internal API

```text
client
  -> POST /predict with Event JSON
  -> api_service writes tmp/api/<request_id>/event.json
  -> api_service runs scripts/run_goal_exec.sh
  -> claude --print receives the /goal-style prompt and event JSON
  -> Claude uses local tools and web search
  -> Claude returns strict JSON in the final_forecast shape (enforced by the prompt plus the harness validator/fallback)
  -> api_service validates with submit_prediction.validator
  -> api_service returns final normalized JSON
  -> api_service logs to logs/api_predictions.jsonl
```

## Evidence Tools

All tools are copied into this standalone repo:

- `market_lookup`
- `kalshi_discovery`
- `sports_lookup`
- `finance_lookup`
- `submit_prediction`
- `sample_tools`

They are not symlinked to the Pi repo.


# Prophet Hacks Rules And Integration Notes

This file records the current working interpretation used by ClaudeProphet.

## Forecasting Track

- The developer quick start's Option 2 shows `POST /predict` receiving one
  event JSON object and returning a prediction object with `probabilities`.
- Organizer clarification on May 18, 2026: timeout is per event, the request
  contains exactly one event, and the evaluator will make roughly 12 separate
  requests for 12 events in the current check. The evaluator does not retry.
- The evaluation phase is expected to send at most about 200 total forecast requests over the 2-week period, with requests arriving every day.
- The normal request body is a single Prophet `Event` JSON object with fields such as `event_ticker`, `market_ticker`, `title`, `description`, `category`, `rules`, `close_time`, and `outcomes`.
- The endpoint should respond with a prediction object, not a chat-completions response.
- The response only needs `probabilities`; extra fields do not matter.
- Binary events may use `p_yes`, but this repo prefers `probabilities`.
- Multi-outcome events are scored across the full probability distribution.
- Multi-outcome events are not always mutually exclusive. For Top-K / winning-set events, each probability should mean "this outcome is in the winning set," so probabilities may sum to K rather than 1. Do not normalize Top-K events down to a single-winner distribution.
- For mutually exclusive outcomes, probabilities should still usually sum near 1.
- Outcome labels in `probabilities[*].market` must exactly match event outcomes.
- The organizer clarification said each event has a 10-minute response window.
- ClaudeProphet should not give Claude the entire 10 minutes. The production
  `predict.hansonwen.dev` route is direct through a VPS reverse proxy, so it can
  use `CLAUDE_API_TIMEOUT=585` with `CLAUDE_FORECAST_RETURN_BUFFER_SECONDS=45`.
  That leaves Claude about 540 seconds for research/finalization while preserving
  response time for parsing, validation, logging, and fallback before the
  evaluator timeout. (Note: the production Mac-mini/VPS/Cloudflare deploy scripts
  remain the legacy Codex-based setup and were not converted.)
- Organizers expect reasonable advanced-model agents to cost less than about $0.30 per forecast, so a 200-request evaluation suggests a rough expected run cost below about $60 if our per-request behavior is similar.
- Optional endpoint self-checks on the Prophet Hacks website include a `GET /health` check and a shorter-timeout format check that posts a sample forecast event. The format check is only a demo shape check; the actual evaluation timeout is longer.
- During actual evaluation, organizers said they will first call `/health` to wake the service, wait briefly, then POST events.

## Submission / Registration

The public official CLI does not expose a `prophet forecast submit` command. The supported server operations are:

- fetch forecast events
- register a team
- register or update a team endpoint
- read the registered endpoint
- read the leaderboard

Therefore, in this repo, "submit to the Prophet server" means registering a reachable `/predict` endpoint with the Prophet server. The server then calls that endpoint for auto-forecasting.

Useful local API helper endpoints:

```text
GET  /prophet/events?status=open
POST /prophet/register-team
POST /prophet/register-endpoint
GET  /prophet/endpoint/{team_name}
GET  /prophet/leaderboard
```

The API key can be provided as `PA_SERVER_API_KEY` or `PROPHETHACKS_SERVER_API_KEY` in `.env`.

## Local Endpoint

ClaudeProphet serves a local endpoint for testing:

```text
GET  /health
POST /predict
```

For a registered endpoint like `https://predict.hansonwen.dev/predict`, the
organizer health check is expected at `https://predict.hansonwen.dev/health`.

By default `npm run serve` binds to `127.0.0.1:8080`, so it is internal to the machine. Use `npm run serve:lan` only when a LAN/tunnel process should expose it.

Claude should save a local `initial` checkpoint within the first 1-2 minutes and
keep updating it as the best current estimate changes. When final research is
complete, Claude should save a `final` checkpoint. If Claude fails or hits the
internal timeout, the API returns the latest valid final checkpoint, then the
latest valid initial checkpoint, then a deterministic fallback instead of
surfacing a 5xx response.

## Sample Datasets

Official sample datasets are used for smoke tests and local validation, not for the final hidden evaluation distribution.

- `sample-sports`: 16 unresolved sports events
- `sample-entertainment`: 13 unresolved entertainment events
- `sample-economics`: 13 unresolved economics events
- `sample-resolved`: 26 resolved events for local scoring examples

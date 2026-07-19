# Prophet Hacks Submission

## Repository

Public GitHub repository:

```text
https://github.com/Hilo-Hilo/CodexProphet
```

License: CC BY 4.0 (Creative Commons Attribution 4.0 International). Use, share,
and adaptation are permitted with attribution. See `LICENSE` and `CITATION.cff`.

## Forecasting Track Endpoint

Production endpoint:

```text
https://predict.hansonwen.dev/predict
```

The developer docs' Option 2 pattern is supported directly: the evaluator may
`POST` one event JSON object to this exact URL, and the service returns a JSON
prediction with a full `probabilities` distribution over the provided event
outcomes and a `rationale`.

Evaluation expectations from organizer Discord/quick-start updates:

- Requests contain one event object per POST. The evaluator makes separate
  requests for separate events.
- The response only needs `probabilities`.
- The evaluator does not retry failed or timed-out requests.
- Up to about 200 total forecast requests over the 2-week evaluation period.
- One event per POST request.
- 10-minute timeout per evaluation request.
- Direct/local standardized runs use a near-full 10-minute lifecycle budget. The
  production `predict.hansonwen.dev` deployment is routed through a direct VPS
  reverse proxy and uses `CLAUDE_API_TIMEOUT=585` plus a 45-second forecast return
  buffer, giving Claude about 540 seconds to forecast while preserving time for
  validation, logging, and the final response before the evaluator deadline. (The
  Mac-mini/VPS/Cloudflare production deploy scripts remain the legacy Codex-based
  setup and were not converted.)
- Requests are expected daily.
- Organizers may first call `/health` to wake the service before POSTing events.
- A website format check may use a shorter browser timeout; treat it as a schema
  smoke test, not as the production latency budget.
- Multi-outcome events may be non-mutually-exclusive. For Top-K / winning-set
  events, probabilities are per-outcome inclusion probabilities and may sum to
  K rather than 1.

Health check:

```text
https://predict.hansonwen.dev/health
```

Public landing page:

```text
https://predict.hansonwen.dev/
```

## Standardized Local Run

The organizer-facing entrypoint is:

```bash
scripts/run_standardized_agent.sh
```

From a fresh clone:

```bash
git clone https://github.com/Hilo-Hilo/CodexProphet.git
cd CodexProphet
claude login
PORT=8080 scripts/run_standardized_agent.sh
```

Then call:

```bash
curl -s http://127.0.0.1:8080/health
.venv/bin/python - <<'PY'
import json
from pathlib import Path
events = json.loads(Path("sample_events/sample-economics.json").read_text())
event = events[0] if isinstance(events, list) else events
Path("tmp/sample-event.json").write_text(json.dumps(event, indent=2) + "\n")
PY
curl -s -X POST http://127.0.0.1:8080/predict \
  -H 'content-type: application/json' \
  --data-binary @tmp/sample-event.json
```

Authentication uses a Claude subscription via `claude login` (or a
`CLAUDE_CODE_OAUTH_TOKEN` from `claude setup-token` for non-interactive use). A
machine already logged into Claude can run without any token. `ANTHROPIC_API_KEY`
is intentionally disabled for forecasts (subscription only, no API key). Do not
commit tokens or local auth files.

## Docker Run

The Docker image is also supported. (Note: the `Dockerfile` and this Docker path
remain the legacy Codex-based setup and were not converted; the Claude harness
runs via the standardized local run above.)

```bash
docker build -t codexprophet .
docker run --rm -p 8080:8080 \
  -e PORT=8080 \
  -e CODEX_PUBLIC_API_MODE=true \
  -e OPENAI_API_KEY="$OPENAI_API_KEY" \
  codexprophet
```

## Logs

Each prediction request is logged locally:

```text
logs/api_predictions.jsonl
logs/prediction_audit.jsonl
logs/prediction_audit.md
```

The audit logs include request receipt, input event payload, final probabilities,
rationale, runtime, and any validation or Claude execution errors. Keys and
secret-like fields are redacted.

If Claude exceeds the internal time budget or fails, the API returns a valid
fallback prediction rather than a 5xx response. During a run, Claude is instructed
to save an early `initial` checkpoint and then a `final` checkpoint through the
local submit tool. The API fallback order is:

1. latest valid final checkpoint,
2. latest valid initial checkpoint,
3. deterministic fallback from embedded market data or conservative priors.

## Secrets

No proprietary API keys are committed. The forecasting agent authenticates with a
Claude subscription via `claude login` (or a `CLAUDE_CODE_OAUTH_TOKEN`), not an
API key; `ANTHROPIC_API_KEY` is intentionally disabled for forecasts. Remaining
runtime secrets are provider data keys supplied by environment variables such as:

```text
PA_SERVER_API_KEY
THE_ODDS_API_KEY
FRED_API_KEY
FINNHUB_API_KEY
ALPHA_VANTAGE_API_KEY
```

## Trading Track

This repository is currently packaged for the Forecasting Track. It does not
claim Trading Track compatibility unless the official trading harness is added
and wired into this repo.

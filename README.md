# ClaudeProphet

Claude-native forecasting harness for Prophet Hacks.

This is a Claude conversion of the original Codex harness. Instead of the Codex
CLI, it drives the **Claude Code CLI** (`claude`) using your **local Claude
subscription** (Claude Code login / OAuth) — no API key. The agent receives
forecasting instructions through `AGENTS.md` and uses local domain skill files
plus the repo's forecasting CLI tools.

Upstream (original Codex version): `https://github.com/Hilo-Hilo/CodexProphet`.

License: [Creative Commons Attribution 4.0 International (CC BY 4.0)](https://creativecommons.org/licenses/by/4.0/). See `LICENSE`.

## Prerequisites

1. **Claude Code CLI**, logged in to your subscription (no API key):

   ```bash
   npm install -g @anthropic-ai/claude-code
   claude login          # sign in with your Claude subscription
   ```

   For a headless/server run instead of an interactive login, generate a
   subscription token and export it:

   ```bash
   claude setup-token     # prints a CLAUDE_CODE_OAUTH_TOKEN value
   export CLAUDE_CODE_OAUTH_TOKEN=...
   ```

   The harness deliberately unsets `ANTHROPIC_API_KEY` for forecasts so runs
   never silently fall back to metered API billing.

2. **Python 3.11+** recommended. (On Python 3.9 the FastAPI/pydantic service will
   not import without the `eval_type_backport` package. Prefer a newer Python,
   e.g. `brew install python@3.12`.)

3. **Node 18+** for the `npm run` entry points.

## Launch

Set up the repo first:

```bash
cd /path/to/claudeprophet
npm run setup
npm run check
```

Interactive Claude forecasting session:

```bash
npm run claude:prophet -- "Will Bitcoin trade above 100000 by Sunday?"
```

or with an event JSON file:

```bash
npm run claude:prophet -- sample_events/sample-economics.json
```

Non-interactive one-shot run (prints strict JSON to stdout):

```bash
npm run claude:prophet:exec -- sample_events/sample-economics.json
```

Standardized API run (starts the Forecasting Track API):

```bash
PORT=8080 scripts/run_standardized_agent.sh
```

This starts the API on `0.0.0.0:8080` by default and exposes:

```text
GET  /health
POST /predict
```

Defaults:

- Model: `claude-opus-4-8`. Override with `CLAUDE_FORECAST_MODEL`.
- Permission mode: `bypassPermissions` for non-interactive `/predict` forecasts,
  so the agent's local evidence tools can reach Kalshi, finance, sports, and
  other network providers without prompts. Override with
  `CLAUDE_FORECAST_PERMISSION_MODE`.
- Active variant: `config/variants.json` -> `v1_market_prior_claude` by default.
  Override with `CLAUDE_FORECAST_VARIANT`.
- Web search: Claude's built-in WebSearch tool (no flag required).
- Auth: local Claude subscription (login or `CLAUDE_CODE_OAUTH_TOKEN`).
- Request budget: actual evaluation is expected to allow up to 10 minutes per
  event. The production route uses `CLAUDE_API_TIMEOUT=585` with
  `CLAUDE_FORECAST_RETURN_BUFFER_SECONDS=45`, giving Claude about 540 seconds for
  forecasting and leaving time for validation, logging, and the final response.
- Working root: repo root.

## Local API

Run the internal API on this machine:

```bash
npm run serve
```

This binds to `127.0.0.1:8080` by default. It is not public.

Endpoints:

```text
GET  /health
POST /predict
GET  /prophet/events?status=open
POST /prophet/register-team
POST /prophet/register-endpoint
GET  /prophet/endpoint/{team_name}
GET  /prophet/leaderboard
```

Prediction smoke test:

```bash
curl -s http://127.0.0.1:8080/health
python - <<'PY'
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

For Prophet server registration, create `.env` with `PA_SERVER_API_KEY` or
`PROPHETHACKS_SERVER_API_KEY`. See `.env.example` for the full set of supported
variables. A local `.env` is ignored by Git.

## Configuration

Copy `.env.example` to `.env` and adjust. Key variables:

```text
CLAUDE_FORECAST_MODEL=claude-opus-4-8         # forecasting model
CLAUDE_FORECAST_PERMISSION_MODE=bypassPermissions
CLAUDE_API_TIMEOUT=540                         # process kill switch (seconds)
CLAUDE_EVALUATION_TIMEOUT=600
CLAUDE_FORECAST_RETURN_BUFFER_SECONDS=20
CLAUDE_CODE_OAUTH_TOKEN=...                    # optional headless subscription token
CLAUDEPROPHET_CLAUDE_CONFIG_DIR=...            # optional: isolate CLAUDE_CONFIG_DIR
```

## How It Works

The launcher builds an initial forecasting prompt and starts Claude with it. The
prompt tells Claude to:

1. Read `AGENTS.md`.
2. Read `docs/context_memory.md`, `docs/market_signal_learnings.md`, and
   `skills/calibration-validation/SKILL.md`.
3. Select other relevant skill files under `skills/`.
4. Use the repo's local CLI tools for market, sports, finance, Kalshi, and
   validation, plus the built-in WebSearch tool.
5. Distinguish mutually exclusive outcome rows from Top-K / multi-correct rows.
6. Save an early `--kind initial` forecast checkpoint within the first 1-2
   minutes.
7. Keep updating the initial checkpoint as the current best forecast improves.
8. Save `--kind final` when the final forecast is ready.
9. Return final strict JSON only.

Each API request gets a local workspace under `tmp/api/<request_id>/`:

```text
event.json
evidence_manifest.json
trace.jsonl
initial_submission.json
final_submission.json
claude_stdout.txt
claude_stderr.txt
claude_final.json
```

The API initializes `evidence_manifest.json` with the active variant and event
payload, then exposes `REQUEST_WORKSPACE`, `EVIDENCE_MANIFEST`, `TRACE_LOG`,
`ACTIVE_VARIANT_ID`, and `ACTIVE_VARIANT_JSON` to the Claude prompt. Claude can
read these files during the run. For important evidence that comes from web
search or another source not automatically logged by a local tool, Claude can
append a concise item:

```bash
.venv/bin/python -m api_service.run_metadata evidence \
  --workspace "$REQUEST_WORKSPACE" \
  --kind "web" \
  --source "native_web_search" \
  --query "<query>" \
  --notes "<what this evidence established>"
```

The API writes `trace.jsonl` lifecycle events such as request receipt, Claude
start/finish, fallback usage, validation failure, and validation success. Claude
may append agent-side milestones, but tracing must never delay a valid forecast.

Because the Claude CLI has no schema-enforcement flag, the final answer is kept
strict-JSON by the run prompt plus the harness's own validator and fallback
ladder — not by a CLI `--output-schema`.

For Top-K or other non-mutually-exclusive events, the returned probabilities are
per-outcome inclusion probabilities and may sum to K. They should not be
normalized to 1 unless the event is actually single-winner / mutually exclusive.

This is intentionally an agentic workflow. Claude decides which tools to call,
what to search, and when it has enough evidence.

## Fallback Behavior

If Claude exceeds the internal time budget or fails, the API returns a valid
fallback prediction rather than a 5xx. The fallback order is:

1. latest valid final checkpoint,
2. latest valid initial checkpoint,
3. a deterministic fallback from embedded market data or conservative priors.

An optional OpenRouter fallback exists but is **disabled by default**; enable it
only by setting `CLAUDE_ALLOW_OPENROUTER_FALLBACK=1` plus `OPENROUTER_API_KEY`.

## Tool Surface

From repo root:

```bash
npm run market:lookup -- --text "..." --category "..." --max-markets 10
npm run market:lookup -- --text "..." --include-history --history-lookback-days 7 --history-trade-limit 50 --history-candle-limit 48
npm run kalshi:discover -- --query "..." --status open --max-markets 100
npm run sports:lookup -- --query "..." --sport auto --include-odds
npm run finance:lookup -- --query "..." --symbols NVDA --asset-type equity
npm run fundamentals:lookup -- --query "nvidia revenue" --symbols NVDA --status open
npm run metadata -- evidence --workspace tmp/api/<request_id> --kind web --source native_web_search --query "..." --notes "..."
npm run metadata -- trace --workspace tmp/api/<request_id> --stage agent_research_complete --message "..."
npm run submit:prediction -- --event event.json --prediction prediction.json
npm run submit:prediction -- --kind initial --event event.json --prediction prediction.json
npm run submit:prediction -- --kind final --event event.json --prediction prediction.json
```

`submit_prediction --kind initial` and `--kind final` save local checkpoints
beside the event file. They do not submit externally. If Claude times out or
returns malformed stdout, the API uses the latest valid final checkpoint, then
the latest valid initial checkpoint, then a deterministic fallback.

`--include-history` is opt-in and currently enriches matched Kalshi markets with
public trade prints and price/volume candles.

Claude also has its normal shell/file/search tools and the built-in WebSearch
tool.

## Deployment (legacy, not converted)

The production deploy scaffolding — `scripts/deploy_mac_mini.sh`,
`scripts/render_start.sh`, `render.yaml`, `Dockerfile`, the Cloudflare tunnel
installer, and the auth watchdog — remains the **original Codex-based setup** and
was intentionally left unconverted. It is not needed to run ClaudeProphet locally
with the `npm run` entry points above. If you adapt it for a Claude deployment,
install the `claude` CLI in that environment and provide subscription auth
(`claude login` or `CLAUDE_CODE_OAUTH_TOKEN`) instead of `OPENAI_API_KEY`.

## Citation

This work is licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
It derives from CodexProphet by Wen & Gui; please cite the upstream work:

```bibtex
@software{wen_codexprophet_2026,
  author  = {Wen, Hanson and Gui, James},
  title   = {{CodexProphet: A Codex-native forecasting harness}},
  year    = {2026},
  url     = {https://github.com/Hilo-Hilo/CodexProphet},
  license = {CC-BY-4.0}
}
```

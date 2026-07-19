#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8080}"

if [[ ! -x ".venv/bin/python" ]]; then
  python3 -m venv .venv
fi

.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt

if ! command -v claude >/dev/null 2>&1; then
  echo "claude CLI is required. Install it before running this script:" >&2
  echo "  npm install -g @anthropic-ai/claude-code" >&2
  echo "Docker users can instead run: docker build -t claudeprophet . && docker run ..." >&2
  exit 2
fi

# ClaudeProphet runs on the local Claude subscription (Claude Code login / OAuth),
# not on an API key. Disable API-key auth so /predict never falls back to metered
# API billing.
unset ANTHROPIC_API_KEY
unset ANTHROPIC_AUTH_TOKEN

if [[ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]]; then
  cfg="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
  if [[ ! -f "$cfg/.credentials.json" ]] \
    && ! { command -v security >/dev/null 2>&1 \
      && security find-generic-password -s "Claude Code-credentials" >/dev/null 2>&1; }; then
    echo "WARNING: could not confirm a logged-in Claude subscription session." >&2
    echo "Run 'claude login' once, or set CLAUDE_CODE_OAUTH_TOKEN from 'claude setup-token'," >&2
    echo "otherwise /predict will fall back when it launches Claude." >&2
  fi
fi

export CLAUDE_PUBLIC_API_MODE="${CLAUDE_PUBLIC_API_MODE:-true}"
export CLAUDE_FORECAST_MODEL="${CLAUDE_FORECAST_MODEL:-claude-opus-4-8}"
export CLAUDE_EVALUATION_TIMEOUT="${CLAUDE_EVALUATION_TIMEOUT:-600}"
export CLAUDE_API_TIMEOUT="${CLAUDE_API_TIMEOUT:-540}"

exec .venv/bin/python -m api_service --host "$HOST" --port "$PORT"

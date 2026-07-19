#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HARNESS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$HARNESS_DIR"
MODEL="${CLAUDE_FORECAST_MODEL:-claude-opus-4-8}"
PERMISSION_MODE="${CLAUDE_FORECAST_PERMISSION_MODE:-bypassPermissions}"
MAX_TURNS="${CLAUDE_FORECAST_MAX_TURNS:-200}"
PROMPT_TEMPLATE="$HARNESS_DIR/prompts/goal_prompt.md"
SYSTEM_PROMPT_FILE="$HARNESS_DIR/prompts/system_prompt.md"
TIME_BUDGET_SECONDS="${CLAUDE_FORECAST_TIME_BUDGET_SECONDS:-540}"
EVALUATION_TIMEOUT_SECONDS="${CLAUDE_FORECAST_EVALUATION_TIMEOUT_SECONDS:-600}"

load_secret_from_env_file() {
  local key="$1"
  local file="$REPO_ROOT/.env"
  if [[ -n "${!key:-}" || ! -f "$file" ]]; then
    return
  fi
  local value
  value="$(python3 - "$file" "$key" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
target = sys.argv[2]
for line in path.read_text(errors="ignore").splitlines():
    if not line or line.lstrip().startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    if key.strip() == target:
        print(value.strip().strip('"').strip("'"))
        break
PY
)"
  if [[ -n "$value" ]]; then
    export "$key=$value"
  fi
}

configure_claude_home() {
  load_secret_from_env_file CLAUDEPROPHET_CLAUDE_CONFIG_DIR
  if [[ -n "${CLAUDEPROPHET_CLAUDE_CONFIG_DIR:-}" ]]; then
    mkdir -p "$CLAUDEPROPHET_CLAUDE_CONFIG_DIR"
    export CLAUDE_CONFIG_DIR="$CLAUDEPROPHET_CLAUDE_CONFIG_DIR"
  fi
}

disable_api_key_auth_env() {
  # ClaudeProphet forecasts run on the local Claude subscription (Claude Code
  # login / OAuth). API-key auth is disabled so runs never silently fall back to
  # metered API billing.
  unset ANTHROPIC_API_KEY
  unset ANTHROPIC_AUTH_TOKEN
}

ensure_claude_auth() {
  # A generated subscription token (from `claude setup-token`) is the explicit
  # non-interactive path and always wins.
  if [[ -n "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]]; then
    return
  fi
  local cfg="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
  if [[ -f "$cfg/.credentials.json" ]]; then
    return
  fi
  # macOS stores the Claude Code login in the login keychain.
  if command -v security >/dev/null 2>&1 \
    && security find-generic-password -s "Claude Code-credentials" >/dev/null 2>&1; then
    return
  fi
  echo "ClaudeProphet requires Claude subscription auth; ANTHROPIC_API_KEY auth is disabled for forecasts." >&2
  echo "Could not confirm a logged-in Claude subscription session." >&2
  echo "Repair with: claude login   (or set CLAUDE_CODE_OAUTH_TOKEN from 'claude setup-token')." >&2
  # Non-fatal: keychain detection is best-effort. Let the claude run surface a
  # precise auth error rather than blocking a session that is actually logged in.
}

run_openrouter_fallback() {
  load_secret_from_env_file OPENROUTER_API_KEY
  load_secret_from_env_file OPENROUTER_FALLBACK_MODEL
  load_secret_from_env_file OPENROUTER_BASE_URL
  if [[ -z "${OPENROUTER_API_KEY:-}" ]]; then
    echo "OPENROUTER_API_KEY is not configured; skipping OpenRouter fallback." >&2
    return 1
  fi
  python3 "$SCRIPT_DIR/openrouter_fallback.py" \
    --event "$INPUT" \
    --workspace "$REQUEST_WORKSPACE" \
    --model "${OPENROUTER_FALLBACK_MODEL:-anthropic/claude-opus-4}" \
    --timeout-seconds "${OPENROUTER_FALLBACK_TIMEOUT_SECONDS:-45}"
}

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <event-json-file-or-task-text>" >&2
  exit 2
fi

INPUT="$*"
if [[ -f "$INPUT" ]]; then
  EVENT_CONTENT="$(cat "$INPUT")"
  REQUEST_WORKSPACE="${CLAUDE_REQUEST_WORKSPACE:-$(cd "$(dirname "$INPUT")" && pwd)}"
else
  EVENT_CONTENT="$INPUT"
  REQUEST_WORKSPACE="${CLAUDE_REQUEST_WORKSPACE:-}"
fi

EVIDENCE_MANIFEST="${CLAUDE_EVIDENCE_MANIFEST:-${REQUEST_WORKSPACE:+$REQUEST_WORKSPACE/evidence_manifest.json}}"
TRACE_LOG="${CLAUDE_TRACE_LOG:-${REQUEST_WORKSPACE:+$REQUEST_WORKSPACE/trace.jsonl}}"
VARIANT_ID="${CLAUDE_FORECAST_VARIANT_ID:-}"
VARIANT_JSON="${CLAUDE_FORECAST_VARIANT_JSON:-}"

PROMPT="$(cat "$PROMPT_TEMPLATE")
EVENT_FILE_OR_TASK: $INPUT
REQUEST_WORKSPACE: $REQUEST_WORKSPACE
EVIDENCE_MANIFEST: $EVIDENCE_MANIFEST
TRACE_LOG: $TRACE_LOG
ACTIVE_VARIANT_ID: $VARIANT_ID
ACTIVE_VARIANT_JSON: $VARIANT_JSON
INTERNAL_TIME_BUDGET_SECONDS: $TIME_BUDGET_SECONDS
EVALUATION_TIMEOUT_SECONDS: $EVALUATION_TIMEOUT_SECONDS

$EVENT_CONTENT"

configure_claude_home
disable_api_key_auth_env
ensure_claude_auth

APPEND_SYSTEM_ARGS=()
if [[ -f "$SYSTEM_PROMPT_FILE" ]]; then
  APPEND_SYSTEM_ARGS=(--append-system-prompt "$(cat "$SYSTEM_PROMPT_FILE")")
fi

CLAUDE_STDOUT_FILE="$(mktemp "${TMPDIR:-/tmp}/claudeprophet-claude-stdout.XXXXXX")"
CLAUDE_STDERR_FILE="$(mktemp "${TMPDIR:-/tmp}/claudeprophet-claude-stderr.XXXXXX")"
trap 'rm -f "$CLAUDE_STDOUT_FILE" "$CLAUDE_STDERR_FILE"' EXIT

cd "$REPO_ROOT"
set +e
claude \
  --print \
  --model "$MODEL" \
  --permission-mode "$PERMISSION_MODE" \
  --max-turns "$MAX_TURNS" \
  --add-dir "$REPO_ROOT" \
  "${APPEND_SYSTEM_ARGS[@]}" \
  "$PROMPT" >"$CLAUDE_STDOUT_FILE" 2> >(tee "$CLAUDE_STDERR_FILE" >&2)
CLAUDE_STATUS=$?
set -e

if [[ "$CLAUDE_STATUS" -eq 0 ]]; then
  cat "$CLAUDE_STDOUT_FILE"
  exit 0
fi

if [[ ! "${CLAUDE_ALLOW_OPENROUTER_FALLBACK:-}" =~ ^(1|true|yes|on)$ ]]; then
  echo "Claude run failed with exit code $CLAUDE_STATUS; OpenRouter fallback is disabled by default." >&2
  if [[ -s "$CLAUDE_STDOUT_FILE" ]]; then
    echo "Claude stdout before failure follows:" >&2
    cat "$CLAUDE_STDOUT_FILE" >&2
  fi
  if [[ -s "$CLAUDE_STDERR_FILE" ]]; then
    echo "Claude stderr before failure follows:" >&2
    cat "$CLAUDE_STDERR_FILE" >&2
  fi
  exit "$CLAUDE_STATUS"
fi

echo "Claude run failed with exit code $CLAUDE_STATUS; trying explicitly enabled OpenRouter fallback." >&2
if [[ -s "$CLAUDE_STDOUT_FILE" ]]; then
  echo "Claude stdout before failure follows:" >&2
  cat "$CLAUDE_STDOUT_FILE" >&2
fi
if [[ -s "$CLAUDE_STDERR_FILE" ]]; then
  echo "Claude stderr before failure follows:" >&2
  cat "$CLAUDE_STDERR_FILE" >&2
fi
run_openrouter_fallback

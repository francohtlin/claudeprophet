#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HARNESS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$HARNESS_DIR"
MODEL="${CLAUDE_FORECAST_MODEL:-claude-opus-4-8}"
PERMISSION_MODE="${CLAUDE_FORECAST_PERMISSION_MODE:-bypassPermissions}"
PROMPT_TEMPLATE="$HARNESS_DIR/prompts/goal_prompt.md"
SYSTEM_PROMPT_FILE="$HARNESS_DIR/prompts/system_prompt.md"

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <event-json-file-or-task-text>" >&2
  exit 2
fi

INPUT="$*"
if [[ -f "$INPUT" ]]; then
  EVENT_CONTENT="$(cat "$INPUT")"
else
  EVENT_CONTENT="$INPUT"
fi

PROMPT="$(cat "$PROMPT_TEMPLATE")
$EVENT_CONTENT"

APPEND_SYSTEM_ARGS=()
if [[ -f "$SYSTEM_PROMPT_FILE" ]]; then
  APPEND_SYSTEM_ARGS=(--append-system-prompt "$(cat "$SYSTEM_PROMPT_FILE")")
fi

cd "$REPO_ROOT"
exec claude \
  --model "$MODEL" \
  --permission-mode "$PERMISSION_MODE" \
  --add-dir "$REPO_ROOT" \
  "${APPEND_SYSTEM_ARGS[@]}" \
  "$PROMPT"

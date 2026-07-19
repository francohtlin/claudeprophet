#!/usr/bin/env bash
set -euo pipefail

REPO="${CODEXPROPHET_GITHUB_REPO:-Hilo-Hilo/CodexProphet}"
TOOL=""
EVENT=""
COMMAND_TEXT=""
ERROR_FILE=""
NOTES=""

usage() {
  cat <<'USAGE'
Usage:
  npm run issue:tool-failure -- \
    --tool "market:lookup" \
    --event tmp/api/<request>/event.json \
    --command "npm run market:lookup -- ..." \
    --error-file tmp/tool.err \
    --notes "fallback used native web search"

Best-effort GitHub issue reporter for live forecast tool failures.
Never include secrets in arguments or files.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tool)
      TOOL="${2:-}"
      shift 2
      ;;
    --event)
      EVENT="${2:-}"
      shift 2
      ;;
    --command)
      COMMAND_TEXT="${2:-}"
      shift 2
      ;;
    --error-file)
      ERROR_FILE="${2:-}"
      shift 2
      ;;
    --notes)
      NOTES="${2:-}"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "$TOOL" ]]; then
  echo "Missing required --tool" >&2
  usage >&2
  exit 2
fi

redact() {
  perl -0pe '
    s/sk-[A-Za-z0-9_-]+/[REDACTED_OPENAI_STYLE_KEY]/g;
    s/sk-or-v1-[A-Za-z0-9_-]+/[REDACTED_OPENROUTER_KEY]/g;
    s/prophet_[A-Za-z0-9_-]+/[REDACTED_PROPHET_KEY]/g;
    s/[A-Za-z0-9_]*(API_KEY|SECRET|TOKEN|PASSWORD)[A-Za-z0-9_]*=[^\s]+/$1=[REDACTED]/gi;
  '
}

event_summary="No event file supplied."
if [[ -n "$EVENT" && -f "$EVENT" ]]; then
  event_summary="$(
    python3 - "$EVENT" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
try:
    event = json.loads(path.read_text())
except Exception as exc:
    print(f"Could not parse event JSON at {path}: {exc}")
    raise SystemExit

fields = {
    "path": str(path),
    "event_ticker": event.get("event_ticker"),
    "market_ticker": event.get("market_ticker"),
    "title": event.get("title"),
    "category": event.get("category"),
    "close_time": event.get("close_time"),
    "outcomes": event.get("outcomes"),
}
print(json.dumps(fields, indent=2, sort_keys=True))
PY
  )"
fi

error_excerpt="No error file supplied."
if [[ -n "$ERROR_FILE" && -f "$ERROR_FILE" ]]; then
  error_excerpt="$(tail -c 8000 "$ERROR_FILE" | redact)"
fi

command_redacted="$(printf '%s' "$COMMAND_TEXT" | redact)"
notes_redacted="$(printf '%s' "$NOTES" | redact)"
timestamp="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
title="[tool-failure] ${TOOL} failed during live forecast"

body_file="$(mktemp)"
cat > "$body_file" <<EOF
## Summary

Tool failure reported by CodexProphet during a live forecast.

- Tool: \`${TOOL}\`
- Timestamp UTC: \`${timestamp}\`
- Fallback used: ${notes_redacted:-Not specified}

## Event

\`\`\`json
${event_summary}
\`\`\`

## Command

\`\`\`bash
${command_redacted:-Not supplied}
\`\`\`

## Error / Output Excerpt

\`\`\`text
${error_excerpt}
\`\`\`

## Expected Follow-Up

Reproduce the command from the repo root, fix the provider/tool failure in a
normal development session, add or update a regression test where practical, and
redeploy manually to the Mac Mini only after review.
EOF

if ! command -v gh >/dev/null 2>&1; then
  echo "gh is unavailable; issue body saved at $body_file" >&2
  exit 0
fi

if ! gh auth status >/dev/null 2>&1; then
  echo "gh is not authenticated; issue body saved at $body_file" >&2
  exit 0
fi

if gh issue create \
  --repo "$REPO" \
  --title "$title" \
  --body-file "$body_file" \
  --label "tool-failure" >/tmp/codexprophet_issue_url.txt 2>/tmp/codexprophet_issue_err.txt; then
  cat /tmp/codexprophet_issue_url.txt
  rm -f "$body_file"
  exit 0
fi

if grep -qi "could not add label" /tmp/codexprophet_issue_err.txt; then
  if gh issue create \
    --repo "$REPO" \
    --title "$title" \
    --body-file "$body_file"; then
    rm -f "$body_file"
    exit 0
  fi
fi

echo "Could not create GitHub issue; issue body saved at $body_file" >&2
cat /tmp/codexprophet_issue_err.txt >&2 || true
exit 0

#!/usr/bin/env bash
set -euo pipefail

PORT="${PORT:-10000}"

if [[ -n "${OPENAI_API_KEY:-}" ]]; then
  printenv OPENAI_API_KEY | codex login --with-api-key >/tmp/codex-login.log 2>&1 || {
    cat /tmp/codex-login.log >&2
    exit 1
  }
elif [[ -n "${CODEX_ACCESS_TOKEN:-}" ]]; then
  printenv CODEX_ACCESS_TOKEN | codex login --with-access-token >/tmp/codex-login.log 2>&1 || {
    cat /tmp/codex-login.log >&2
    exit 1
  }
else
  echo "WARNING: neither OPENAI_API_KEY nor CODEX_ACCESS_TOKEN is set; /predict will fail when it launches Codex." >&2
fi

codex login status || true

exec uvicorn api_service.app:app --host 0.0.0.0 --port "$PORT"

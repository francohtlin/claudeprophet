#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${CODEXPROPHET_REPO_URL:-https://github.com/Hilo-Hilo/CodexProphet.git}"
DEPLOY_DIR="${CODEXPROPHET_DEPLOY_DIR:-$HOME/CodexProphet}"
BRANCH="${CODEXPROPHET_BRANCH:-main}"
PORT="${CODEXPROPHET_PORT:-8080}"
PLIST_ID="${CODEXPROPHET_LAUNCH_AGENT_ID:-dev.hansonwen.codexprophet.api}"
PLIST_PATH="$HOME/Library/LaunchAgents/$PLIST_ID.plist"
LOG_DIR="$DEPLOY_DIR/logs"

assert_safe_to_deploy() {
  if [[ "${CODEXPROPHET_FORCE_DEPLOY:-}" =~ ^(1|true|yes|on)$ ]]; then
    echo "CODEXPROPHET_FORCE_DEPLOY is set; skipping active forecast drain checks."
    return
  fi

  local health_json active
  health_json="$(curl -fsS "http://127.0.0.1:$PORT/health" 2>/dev/null || true)"
  if [[ -n "$health_json" ]]; then
    active="$(
      HEALTH_JSON="$health_json" python3 - <<'PY'
import json
import os

try:
    payload = json.loads(os.environ["HEALTH_JSON"])
except Exception:
    print("")
else:
    value = payload.get("active_forecasts")
    print("" if value is None else value)
PY
    )"
    if [[ "$active" =~ ^[0-9]+$ ]] && (( active > 0 )); then
      echo "Refusing to deploy: $active active /predict forecast(s). Set CODEXPROPHET_FORCE_DEPLOY=1 to override." >&2
      exit 2
    fi
  fi

  if pgrep -f "scripts/run_goal_exec.sh|codex exec" >/dev/null 2>&1; then
    echo "Refusing to deploy: forecast child process still running. Set CODEXPROPHET_FORCE_DEPLOY=1 to override." >&2
    pgrep -af "scripts/run_goal_exec.sh|codex exec" >&2 || true
    exit 2
  fi
}

ensure_env_flag() {
  local key="$1"
  local value="$2"
  local file="$DEPLOY_DIR/.env"
  touch "$file"
  if grep -q "^${key}=" "$file"; then
    perl -0pi -e "s/^${key}=.*$/${key}=${value}/m" "$file"
  else
    printf '\n%s=%s\n' "$key" "$value" >> "$file"
  fi
}

assert_safe_to_deploy

if [[ -d "$DEPLOY_DIR/.git" ]]; then
  git -C "$DEPLOY_DIR" fetch origin --prune
  git -C "$DEPLOY_DIR" reset --hard "origin/$BRANCH"
else
  git clone --branch "$BRANCH" "$REPO_URL" "$DEPLOY_DIR"
fi

cd "$DEPLOY_DIR"
mkdir -p "$LOG_DIR" "$HOME/Library/LaunchAgents"
ensure_env_flag CODEX_PUBLIC_API_MODE true
ensure_env_flag CODEX_API_TIMEOUT "${CODEXPROPHET_PUBLIC_CODEX_TIMEOUT:-585}"
ensure_env_flag CODEX_FORECAST_RETURN_BUFFER_SECONDS "${CODEXPROPHET_FORECAST_RETURN_BUFFER_SECONDS:-45}"
ensure_env_flag CODEX_OPENCLAW_OBSERVER_ENABLED "${CODEXPROPHET_OPENCLAW_OBSERVER_ENABLED:-true}"
ensure_env_flag CODEX_OPENCLAW_OBSERVER_CHANNEL "${CODEXPROPHET_OPENCLAW_OBSERVER_CHANNEL:-telegram}"
if [[ -n "${CODEXPROPHET_OPENCLAW_OBSERVER_TO:-}" ]]; then
  ensure_env_flag CODEX_OPENCLAW_OBSERVER_TO "$CODEXPROPHET_OPENCLAW_OBSERVER_TO"
fi
if [[ -n "${CODEXPROPHET_OPENCLAW_OBSERVER_FAILURE_EMAIL_TO:-}" ]]; then
  ensure_env_flag CODEX_OPENCLAW_OBSERVER_FAILURE_EMAIL_TO "$CODEXPROPHET_OPENCLAW_OBSERVER_FAILURE_EMAIL_TO"
fi
if [[ -n "${CODEXPROPHET_OPENCLAW_OBSERVER_SUCCESS_EMAIL_TO:-}" ]]; then
  ensure_env_flag CODEX_OPENCLAW_OBSERVER_SUCCESS_EMAIL_TO "$CODEXPROPHET_OPENCLAW_OBSERVER_SUCCESS_EMAIL_TO"
fi
ensure_env_flag CODEX_OPENCLAW_OBSERVER_SUCCESS_EMAIL_ACCOUNT "${CODEXPROPHET_OPENCLAW_OBSERVER_SUCCESS_EMAIL_ACCOUNT:-wenhanson0@gmail.com}"
ensure_env_flag CODEX_OPENCLAW_OBSERVER_FAILURE_EMAIL_ACCOUNT "${CODEXPROPHET_OPENCLAW_OBSERVER_FAILURE_EMAIL_ACCOUNT:-wenhanson0@gmail.com}"

npm run setup
npm run check

cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$PLIST_ID</string>
  <key>WorkingDirectory</key>
  <string>$DEPLOY_DIR</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/zsh</string>
    <string>-lc</string>
    <string>cd "$DEPLOY_DIR" &amp;&amp; exec .venv/bin/python -m api_service --host 127.0.0.1 --port "$PORT"</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>$LOG_DIR/api_service.out</string>
  <key>StandardErrorPath</key>
  <string>$LOG_DIR/api_service.err</string>
</dict>
</plist>
PLIST

launchctl bootout "gui/$(id -u)" "$PLIST_PATH" >/dev/null 2>&1 || true
pkill -f "python -m api_service --host 127.0.0.1 --port $PORT" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"
launchctl enable "gui/$(id -u)/$PLIST_ID" >/dev/null 2>&1 || true
launchctl kickstart -k "gui/$(id -u)/$PLIST_ID"

for _ in $(seq 1 20); do
  if curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null; then
    curl -fsS "http://127.0.0.1:$PORT/health"
    printf '\n'
    "$DEPLOY_DIR/scripts/install_auth_watchdog_launchd.sh"
    exit 0
  fi
  sleep 1
done

echo "CodexProphet API did not become healthy on 127.0.0.1:$PORT" >&2
tail -80 "$LOG_DIR/api_service.err" >&2 || true
exit 1

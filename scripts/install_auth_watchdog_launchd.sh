#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="${CODEXPROPHET_DEPLOY_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
PLIST_ID="${CODEX_AUTH_WATCHDOG_LAUNCH_AGENT_ID:-dev.hansonwen.codexprophet.auth-watchdog}"
PLIST_PATH="$HOME/Library/LaunchAgents/$PLIST_ID.plist"
INTERVAL_SECONDS="${CODEX_AUTH_WATCHDOG_INTERVAL_SECONDS:-1800}"
PYTHON_BIN="${CODEX_AUTH_WATCHDOG_PYTHON:-$DEPLOY_DIR/.venv/bin/python}"
LOG_DIR="$DEPLOY_DIR/logs/auth-watchdog"

mkdir -p "$HOME/Library/LaunchAgents" "$LOG_DIR"

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
    <string>cd "$DEPLOY_DIR" &amp;&amp; exec "$PYTHON_BIN" scripts/codex_auth_watchdog.py --json</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>StartInterval</key>
  <integer>$INTERVAL_SECONDS</integer>
  <key>ProcessType</key>
  <string>Background</string>
  <key>StandardOutPath</key>
  <string>$LOG_DIR/launchd.out</string>
  <key>StandardErrorPath</key>
  <string>$LOG_DIR/launchd.err</string>
</dict>
</plist>
PLIST

launchctl bootout "gui/$(id -u)" "$PLIST_PATH" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"
launchctl enable "gui/$(id -u)/$PLIST_ID" >/dev/null 2>&1 || true
launchctl kickstart -k "gui/$(id -u)/$PLIST_ID"

echo "$PLIST_ID installed at $PLIST_PATH"

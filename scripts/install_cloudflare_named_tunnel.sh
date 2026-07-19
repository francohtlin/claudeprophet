#!/usr/bin/env bash
set -euo pipefail

HOSTNAME="${1:-predict.hansonwen.dev}"
SERVICE_URL="${CODEXPROPHET_SERVICE_URL:-http://127.0.0.1:8080}"
TUNNEL_NAME="${CODEXPROPHET_TUNNEL_NAME:-codexprophet}"
CONFIG_DIR="$HOME/.cloudflared"
CONFIG_PATH="$CONFIG_DIR/config.yml"
PLIST_ID="${CODEXPROPHET_TUNNEL_LAUNCH_AGENT_ID:-dev.hansonwen.codexprophet.tunnel}"
PLIST_PATH="$HOME/Library/LaunchAgents/$PLIST_ID.plist"
LOG_DIR="$HOME/CodexProphet/logs"

if [[ ! -f "$CONFIG_DIR/cert.pem" ]]; then
  echo "Missing $CONFIG_DIR/cert.pem. Run: cloudflared tunnel login" >&2
  exit 2
fi

mkdir -p "$CONFIG_DIR" "$HOME/Library/LaunchAgents" "$LOG_DIR"

if ! cloudflared tunnel list 2>/dev/null | awk '{print $2}' | grep -qx "$TUNNEL_NAME"; then
  cloudflared tunnel create "$TUNNEL_NAME"
fi

TUNNEL_ID="$(cloudflared tunnel list | awk -v name="$TUNNEL_NAME" '$2 == name {print $1; exit}')"
if [[ -z "$TUNNEL_ID" ]]; then
  echo "Could not find tunnel id for $TUNNEL_NAME" >&2
  exit 1
fi

CREDENTIALS_FILE="$CONFIG_DIR/$TUNNEL_ID.json"

cat > "$CONFIG_PATH" <<YAML
tunnel: $TUNNEL_ID
credentials-file: $CREDENTIALS_FILE

ingress:
  - hostname: $HOSTNAME
    service: $SERVICE_URL
  - service: http_status:404
YAML

cloudflared tunnel route dns "$TUNNEL_NAME" "$HOSTNAME"

cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$PLIST_ID</string>
  <key>ProgramArguments</key>
  <array>
    <string>/opt/homebrew/bin/cloudflared</string>
    <string>tunnel</string>
    <string>--config</string>
    <string>$CONFIG_PATH</string>
    <string>run</string>
    <string>$TUNNEL_NAME</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>$LOG_DIR/cloudflared.out</string>
  <key>StandardErrorPath</key>
  <string>$LOG_DIR/cloudflared.err</string>
</dict>
</plist>
PLIST

launchctl bootout "gui/$(id -u)" "$PLIST_PATH" >/dev/null 2>&1 || true
pkill -f "cloudflared tunnel.*$TUNNEL_NAME" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"
launchctl enable "gui/$(id -u)/$PLIST_ID" >/dev/null 2>&1 || true
launchctl kickstart -k "gui/$(id -u)/$PLIST_ID"

echo "Cloudflare named tunnel is configured for https://$HOSTNAME -> $SERVICE_URL"

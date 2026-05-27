#!/usr/bin/env bash
set -euo pipefail

# Usage: serve.sh <image-path> [minutes]

PORT=7890
CACHE_DIR="$HOME/.cache/serve-image"
PID_FILE="$CACHE_DIR/server.pid"
LOG_FILE="$CACHE_DIR/server.log"
TAILSCALE_CLI="/Applications/Tailscale.app/Contents/MacOS/Tailscale"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- Validate args ---
if [[ $# -lt 1 ]]; then
  echo "Usage: serve.sh <image-path> [minutes]" >&2
  exit 1
fi

RAW_PATH="$1"
MINUTES="${2:-30}"

# Resolve to absolute path
if command -v realpath &>/dev/null; then
  IMAGE="$(realpath "$RAW_PATH")"
else
  IMAGE="$(python3 -c "import os,sys; print(os.path.abspath(sys.argv[1]))" "$RAW_PATH")"
fi

if [[ ! -f "$IMAGE" ]]; then
  echo "Error: file not found: $IMAGE" >&2
  exit 1
fi

FILENAME="$(basename "$IMAGE")"

mkdir -p "$CACHE_DIR"

# --- Kill previous server if running ---
if [[ -f "$PID_FILE" ]]; then
  OLD_PID="$(cat "$PID_FILE")"
  if kill -0 "$OLD_PID" 2>/dev/null; then
    COMM="$(ps -p "$OLD_PID" -o comm= 2>/dev/null || true)"
    if [[ "$COMM" == *python* ]]; then
      kill "$OLD_PID" 2>/dev/null || true
    fi
  fi
  rm -f "$PID_FILE"
fi

# --- Discover Tailscale IP ---
TAILSCALE_IP=""
if [[ -x "$TAILSCALE_CLI" ]]; then
  TAILSCALE_IP="$("$TAILSCALE_CLI" ip -4 2>/dev/null | head -1 || true)"
fi

if [[ -z "$TAILSCALE_IP" ]]; then
  # Fallback: parse ifconfig for 100.x.x.x
  TAILSCALE_IP="$(ifconfig 2>/dev/null | grep -oE 'inet 100\.[0-9]+\.[0-9]+\.[0-9]+' | awk '{print $2}' | head -1 || true)"
fi

if [[ -z "$TAILSCALE_IP" ]]; then
  echo "Error: no Tailscale IP found. Is Tailscale connected?" >&2
  exit 1
fi

# --- Start server ---
nohup python3 "$SCRIPT_DIR/server.py" "$IMAGE" "$PORT" "$MINUTES" > "$LOG_FILE" 2>&1 &
SERVER_PID=$!
disown "$SERVER_PID"
echo "$SERVER_PID" > "$PID_FILE"

sleep 0.5

if ! kill -0 "$SERVER_PID" 2>/dev/null; then
  echo "Error: server failed to start. Log:" >&2
  cat "$LOG_FILE" >&2
  exit 1
fi

# --- Build URL and copy to clipboard ---
URL="http://${TAILSCALE_IP}:${PORT}/${FILENAME}"
echo "$URL" | pbcopy

EXPIRY="$(date -v +"${MINUTES}M" "+%H:%M" 2>/dev/null || date -d "+${MINUTES} minutes" "+%H:%M" 2>/dev/null || echo "in ${MINUTES} minutes")"

echo ""
echo "  Serving: $IMAGE"
echo "  URL:     $URL"
echo "  Copied:  URL is on your clipboard"
echo "  Expires: ~${EXPIRY} (${MINUTES} min)"
echo ""

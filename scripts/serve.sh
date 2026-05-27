#!/usr/bin/env bash
# Back-compat shim: spawn the multi-image daemon if needed, register the given
# image via its localhost control plane, and print the resulting URL.
#
# Usage: serve.sh <image-path> [minutes]
#
# Existing callers (the serve-image SKILL, show-me, etc.) continue to work
# unchanged — only the URL shape changes (now tokenized).

set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: serve.sh <image-path> [minutes]" >&2
  exit 1
fi

RAW_PATH="$1"
MINUTES="${2:-120}"
PORT="${SERVE_IMAGE_PORT:-7890}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

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

# Ensure the daemon is running.
bash "$SCRIPT_DIR/daemon_spawn.sh"

# Register the image.
RESPONSE="$(
  python3 - "$IMAGE" "$MINUTES" "$PORT" <<'PYEOF'
import json, sys, urllib.request, urllib.error
path, minutes, port = sys.argv[1], float(sys.argv[2]), int(sys.argv[3])
req = urllib.request.Request(
    f"http://127.0.0.1:{port}/control/register",
    data=json.dumps({"path": path, "minutes": minutes}).encode(),
    headers={"Content-Type": "application/json"},
    method="POST",
)
try:
    with urllib.request.urlopen(req, timeout=5) as resp:
        print(resp.read().decode())
except urllib.error.HTTPError as e:
    print(e.read().decode(), file=sys.stderr)
    sys.exit(2)
PYEOF
)"

URL="$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['url'])" "$RESPONSE")"
TOKEN="$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['token'])" "$RESPONSE")"

# Copy URL to clipboard (macOS).
if command -v pbcopy &>/dev/null; then
  printf "%s" "$URL" | pbcopy
fi

EXPIRY="$(date -v +"${MINUTES}M" "+%H:%M" 2>/dev/null || date -d "+${MINUTES} minutes" "+%H:%M" 2>/dev/null || echo "in ${MINUTES} minutes")"

cat <<EOF

  Serving: $IMAGE
  URL:     $URL
  Token:   $TOKEN
  Copied:  URL is on your clipboard
  Expires: ~${EXPIRY} (${MINUTES} min)

EOF

#!/usr/bin/env bash
# Ensure the serve-image daemon is running on $PORT. Idempotent and flock'd
# so concurrent callers don't race to bind the port.
#
# Exits 0 once /control/health returns 200, non-zero on failure.

set -euo pipefail

PORT="${SERVE_IMAGE_PORT:-7890}"
CACHE_DIR="$HOME/.cache/serve-image"
LOCK_FILE="$CACHE_DIR/spawn.lock"
PID_FILE="$CACHE_DIR/server.pid"
LOG_FILE="$CACHE_DIR/server.log"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVER_PY="$SCRIPT_DIR/server.py"

mkdir -p "$CACHE_DIR"

health_check() {
  curl -fsS --max-time 1 "http://127.0.0.1:${PORT}/control/health" >/dev/null 2>&1
}

# Fast path: already up.
if health_check; then
  exit 0
fi

# Serialize spawn attempts across concurrent callers using python's fcntl.flock.
# We re-exec this script under the lock; the inner invocation skips re-locking.
if [[ "${SERVE_IMAGE_SPAWN_LOCKED:-}" != "1" ]]; then
  exec env SERVE_IMAGE_SPAWN_LOCKED=1 python3 - "$0" "$LOCK_FILE" <<'PYEOF'
import fcntl, os, sys, subprocess
script, lock_path = sys.argv[1], sys.argv[2]
fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
fcntl.flock(fd, fcntl.LOCK_EX)
sys.exit(subprocess.call(["bash", script]))
PYEOF
fi

# Re-check inside the lock — another process may have just started it.
if health_check; then
  exit 0
fi

# Reap stale PID if present.
if [[ -f "$PID_FILE" ]]; then
  OLD_PID="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "$OLD_PID" ]] && ! kill -0 "$OLD_PID" 2>/dev/null; then
    rm -f "$PID_FILE"
  fi
fi

# If the port is held by some other process (legacy single-image server,
# orphan), kill the holder so we can take over cleanly.
LINGERING="$(lsof -ti :"$PORT" 2>/dev/null || true)"
if [[ -n "$LINGERING" ]]; then
  echo "$LINGERING" | xargs kill -9 2>/dev/null || true
  sleep 0.3
fi

# Spawn detached. nohup + setsid (where available) so the daemon survives
# the calling shell exiting.
if command -v setsid >/dev/null 2>&1; then
  setsid nohup python3 "$SERVER_PY" >>"$LOG_FILE" 2>&1 < /dev/null &
else
  nohup python3 "$SERVER_PY" >>"$LOG_FILE" 2>&1 < /dev/null &
fi
disown || true

# Wait up to 3s for the daemon to start accepting on the port.
for _ in $(seq 1 30); do
  if health_check; then
    exit 0
  fi
  sleep 0.1
done

echo "serve-image daemon failed to start; see $LOG_FILE" >&2
tail -20 "$LOG_FILE" >&2 || true
exit 1

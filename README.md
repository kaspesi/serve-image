# serve-image

Serve local image files over HTTP on a fixed Tailscale-accessible port (default `7890`) so they're reachable from any device on your tailnet.

This is a Claude Code skill packaged as a standalone repo. It's consumed as a git submodule by `agentic-dev-config` and surfaced to the model two ways:

- **MCP tools** — `serve_image`, `list_served`, `revoke`, `purge_all`, `extend`. Registered globally by `agentic-dev-config/install.sh`.
- **Shell shim** — `scripts/serve.sh <path> [minutes]` for non-MCP contexts and downstream skills (e.g. `show-me`) that compose on top.

Both paths talk to the same long-lived daemon, so images coexist and state is shared across every Claude session on the machine.

## Components

- `scripts/server.py` — the HTTP daemon. Pure stdlib, multi-image, per-image TTL, **disk-backed** (blobs streamed from `~/.cache/serve-image/blobs/<token>`, no image bytes in RAM). Listens on `0.0.0.0:<PORT>`; exposes a localhost-only `/control/*` plane for the MCP server and shim to use.
- `scripts/daemon_spawn.sh` — idempotent, `fcntl.flock`-serialized spawner. Safe to call from any session; portable to macOS (no `flock(1)` dependency).
- `scripts/mcp_server.py` — stdlib JSON-RPC stdio MCP server. Spawns the daemon lazily on first tool call.
- `scripts/serve.sh` — back-compat shim. Same CLI as before; URL shape now includes a token segment.

## Disk footprint

Everything lives under `~/.cache/serve-image/`. Bounded so it can't bloat over time:

| File | Purpose | Cap |
|------|---------|-----|
| `blobs/<token>` | Image data (copied from source at register, deleted on revoke/expire/purge). Streamed to clients chunk by chunk. | 50 MiB per image, 1 GiB total live |
| `server.log[.1, .2]` | Rotating daemon log (per-request lines; `/control/health` polls suppressed) | ~3 MiB (1 MiB × 2 backups + active) |
| `server.pid` | Current daemon PID | few bytes |
| `spawn.lock` | Mutex for concurrent spawn attempts | few bytes |

The `blobs/` directory is wiped at daemon startup (the in-memory registry doesn't survive a restart, so any pre-existing blobs are orphans). RAM stays flat regardless of how large the served images are.

## URL shape

`http://<tailscale-ip>:7890/<token>/<filename>` — every image gets an unguessable 11-character token. Old root-style URLs (`http://<ip>:7890/<filename>`) are no longer served; this is a deliberate break to enable coexistence.

## Quick test

```bash
bash scripts/serve.sh /path/to/some.png 5
# → prints URL, copies to clipboard, daemon stays up
curl -fsS http://127.0.0.1:7890/control/list | python3 -m json.tool
```

## Configuration

- `SERVE_IMAGE_PORT` (default `7890`) — override the port.
- `~/.cache/serve-image/` — PID, log, lock, and blobs.

Per-image and total size caps, log rotation thresholds, and TTL defaults are constants near the top of `scripts/server.py` — adjust there if your workflow needs different limits.

## Requirements

- Python 3 (stdlib only — no `pip install` needed).
- Tailscale running, for off-device access (the daemon still binds `0.0.0.0` either way; if there's no Tailscale IP, URLs fall back to `127.0.0.1`).

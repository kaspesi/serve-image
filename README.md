# serve-image

Serve local image files over HTTP on a fixed Tailscale-accessible port (default `7890`) so they're reachable from any device on your tailnet.

This is a Claude Code skill packaged as a standalone repo. It's consumed as a git submodule by `agentic-dev-config` and surfaced to the model two ways:

- **MCP tools** — `serve_image`, `list_served`, `revoke`, `purge_all`, `extend`. Registered globally by `agentic-dev-config/install.sh`.
- **Shell shim** — `scripts/serve.sh <path> [minutes]` for non-MCP contexts and downstream skills (e.g. `show-me`) that compose on top.

Both paths talk to the same long-lived daemon, so images coexist and state is shared across every Claude session on the machine.

## Components

- `scripts/server.py` — the HTTP daemon. Pure stdlib, multi-image, per-image TTL. Listens on `0.0.0.0:<PORT>`; exposes a localhost-only `/control/*` plane for the MCP server and shim to use.
- `scripts/daemon_spawn.sh` — idempotent, `flock`-serialized spawner. Safe to call from any session.
- `scripts/mcp_server.py` — stdlib JSON-RPC stdio MCP server. Spawns the daemon lazily on first tool call.
- `scripts/serve.sh` — back-compat shim. Same CLI as before; URL shape now includes a token segment.

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
- `~/.cache/serve-image/` — PID file, log file, flock file.

## Requirements

- Python 3 (stdlib only — no `pip install` needed).
- Tailscale running, for off-device access (the daemon still binds `0.0.0.0` either way; if there's no Tailscale IP, URLs fall back to `127.0.0.1`).

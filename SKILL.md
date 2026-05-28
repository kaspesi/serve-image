---
name: serve-image
description: Serve one or more local images over HTTP on a fixed Tailscale-accessible port. Triggers on "serve image", "view on phone", "share image", "send to phone", "share this on tailscale".
allowed-tools: Bash, ToolSearch
argument-hint: "<path-to-image> [duration-minutes]"
---

Serve local image files over HTTP so they're accessible from any device on the Tailscale network (e.g. your phone). Backed by a single long-lived daemon on port 7890 that hosts every image at a unique tokenized URL: `http://<tailscale-ip>:7890/<token>/<filename>`. Multiple images coexist; each has its own TTL.

## How to serve an image

Prefer the MCP tools when available — they let you reason about what's currently being served, revoke or extend specific images, and purge state. The `serve.sh` shim still works for simple one-shot serving and for downstream skills that call it directly.

### Option A — MCP tools (preferred)

Load the schemas, then call the relevant tool:

```
ToolSearch(query: "select:mcp__serve-image__serve_image,mcp__serve-image__list_served,mcp__serve-image__revoke,mcp__serve-image__purge_all,mcp__serve-image__extend", max_results: 5)
```

Tools:

- `mcp__serve-image__serve_image({ path, minutes? })` — register an image. Default TTL is 120 minutes. Returns `{url, token, expires_at, ...}`.
- `mcp__serve-image__list_served()` — see every image currently being served, with URLs, expirations, and hit counts. **Call this first if the user might want to know what's already live.**
- `mcp__serve-image__revoke({ token })` — stop serving one specific image.
- `mcp__serve-image__purge_all()` — clear the registry entirely.
- `mcp__serve-image__extend({ token, minutes? })` — bump a TTL.

If the MCP tools aren't loaded yet, `ToolSearch` will surface them — they're registered globally via `install.sh`. If they truly aren't available (fresh machine, install.sh not run), fall back to Option B.

### Option B — shell shim

```bash
bash "${CLAUDE_SKILL_DIR}/scripts/serve.sh" <absolute-path> [minutes]
```

- Default duration is 120 minutes.
- Spawns the daemon if not already running, registers the image, prints the URL, copies it to the clipboard.
- Same daemon as the MCP tools — they share state.

Resolve relative paths first with `realpath "<path>"`, or `python3 -c "import os,sys; print(os.path.abspath(sys.argv[1]))" "<path>"`.

## After serving

Report the URL prominently. Tell the user:

- The URL works from any device on their Tailscale network.
- It expires after the chosen number of minutes (default 2 hours).
- macOS may show a firewall dialog on first run — click Allow.
- Other images may still be live; use `list_served` to check, `revoke`/`purge_all` to clean up.

## Architecture (for skills that compose on this)

- One daemon process per machine, listening on `0.0.0.0:7890`.
- **Disk-backed, never in-memory.** On register, the source file is copied to `~/.cache/serve-image/blobs/<token>` (`shutil.copyfile` — bytes go disk → disk, not through Python memory). On every request the blob is streamed back from disk in 64 KiB chunks straight to the socket; the full image is never buffered in RAM. The blob is the snapshot, so the source file on disk can change, move, or be deleted after registration without affecting served bytes. Blobs are deleted on revoke / expire / purge, and the entire `blobs/` dir is wiped on daemon startup.
- Limits: 50 MiB per image, 1 GiB total live blobs.
- Control plane (`/control/register`, `/control/list`, `/control/revoke`, `/control/purge`, `/control/extend`, `/control/health`) is localhost-only — only this machine can mutate the registry.
- A background sweeper evicts expired entries every 30 seconds.
- PID at `~/.cache/serve-image/server.pid`; logs at `~/.cache/serve-image/server.log`. `daemon_spawn.sh` uses `flock` so concurrent callers don't race to bind the port.

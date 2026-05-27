# serve-image

Claude Code skill to serve images over Tailscale.

Starts a local HTTP server on a fixed port and exposes any image file via a Tailscale-accessible URL — open it on your phone or any device on the same Tailscale network.

## Requirements

- Python 3
- Tailscale (macOS app at `/Applications/Tailscale.app`)
- macOS (uses `pbcopy` for clipboard)

## Install

Clone or symlink this directory into `~/.claude/skills/serve-image/`:

```bash
git clone https://github.com/kaspesi/serve-image ~/.claude/skills/serve-image
```

Or symlink from your agentic-dev-config checkout:

```bash
ln -s /path/to/agentic-dev-config/claude/skills/serve-image ~/.claude/skills/serve-image
```

## Usage

Invoke via `/serve-image` in Claude Code, or ask Claude to "serve image", "view on phone", "share image", etc.

Claude will resolve the path, start the server, and report the URL (also copied to clipboard).

## Details

- **Port**: 7890 (fixed)
- **Binding**: `0.0.0.0` — accessible from any Tailscale peer
- **Auto-cleanup**: 30 minutes by default; pass a second argument for a custom duration
- **One server at a time**: starting a new serve kills the previous one

---
name: serve-image
description: Serve a local image over HTTP on a fixed Tailscale-accessible port. Triggers on "serve image", "view on phone", "share image", "send to phone", "share this on tailscale".
allowed-tools: Bash
argument-hint: "<path-to-image> [duration-minutes]"
---

Serve a local image file over HTTP so it's accessible from any device on the Tailscale network (e.g., your phone).

## Steps

1. Resolve the image path to absolute:
   ```bash
   realpath "<path>"
   ```
   If `realpath` is unavailable, use: `python3 -c "import os,sys; print(os.path.abspath(sys.argv[1]))" "<path>"`

2. Run the serve script:
   ```bash
   bash "${CLAUDE_SKILL_DIR}/scripts/serve.sh" <absolute-path> [minutes]
   ```
   - Default duration is 30 minutes
   - Pass a longer value (e.g. `120`) if the user asks for extended availability

3. After running, **report the URL prominently** to the user.

4. Tell the user:
   - The server auto-stops after N minutes
   - The URL has been copied to the clipboard
   - macOS may show a firewall dialog on first run — click Allow

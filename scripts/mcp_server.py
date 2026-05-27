#!/usr/bin/env python3
"""Stdlib MCP stdio server for serve-image.

Implements just enough of the MCP protocol (JSON-RPC 2.0 over line-delimited
stdio) to expose 5 tools backed by the local serve-image HTTP daemon:

  serve_image(path, minutes=120) → {url, token, expires_at, ...}
  list_served()                  → {items: [...]}
  revoke(token)                  → {revoked: bool}
  purge_all()                    → {removed: N}
  extend(token, minutes)         → {expires_at}

All logging goes to stderr — stdout is reserved for JSON-RPC frames.
Tool errors are reported as tools/call success with isError=true (per spec),
not as JSON-RPC errors.

Spawns the daemon lazily on the first tool call via daemon_spawn.sh.
"""

from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional

PORT = int(os.environ.get("SERVE_IMAGE_PORT", "7890"))
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DAEMON_SPAWN = os.path.join(SCRIPT_DIR, "daemon_spawn.sh")
SERVER_NAME = "serve-image"
SERVER_VERSION = "2.0.0"

PROTOCOL_VERSIONS = {"2025-06-18", "2025-03-26", "2024-11-05"}


def log(msg: str) -> None:
    print(f"[serve-image-mcp] {msg}", file=sys.stderr, flush=True)


def ensure_daemon() -> Optional[str]:
    """Spawn the daemon if not running. Returns None on success, error string otherwise."""
    try:
        subprocess.run(
            ["bash", DAEMON_SPAWN],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=10,
        )
        return None
    except subprocess.CalledProcessError as e:
        return f"daemon_spawn failed: {e.stderr.decode(errors='replace')}"
    except subprocess.TimeoutExpired:
        return "daemon_spawn timed out"
    except FileNotFoundError:
        return f"daemon_spawn.sh not found at {DAEMON_SPAWN}"


def http_call(method: str, path: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    url = f"http://127.0.0.1:{PORT}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8") or "{}") | {"_http_status": e.code}
        except Exception:
            return {"error": f"http_{e.code}", "_http_status": e.code}
    except urllib.error.URLError as e:
        return {"error": "connection_failed", "detail": str(e)}


def fmt_expiry(epoch: float) -> str:
    return datetime.datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M:%S")


def fmt_remaining(epoch: float) -> str:
    secs = max(0, int(epoch - time.time()))
    mins, s = divmod(secs, 60)
    h, m = divmod(mins, 60)
    if h:
        return f"{h}h{m:02d}m"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


# ── Tool definitions ─────────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "serve_image",
        "description": (
            "Serve a local image over HTTP on the Tailscale network. "
            "Returns a unique URL that works from any device on the tailnet. "
            "Multiple images can be served concurrently. Default TTL is 2 hours."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path to the image file on this machine.",
                },
                "minutes": {
                    "type": "number",
                    "description": "How long the URL should remain live, in minutes. Default 120.",
                    "default": 120,
                },
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_served",
        "description": "List all images currently being served, with their URLs, expirations, and hit counts.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "revoke",
        "description": "Stop serving a specific image by its token. The URL will start returning 404 immediately.",
        "inputSchema": {
            "type": "object",
            "properties": {"token": {"type": "string", "description": "Token returned by serve_image."}},
            "required": ["token"],
            "additionalProperties": False,
        },
    },
    {
        "name": "purge_all",
        "description": "Stop serving every image. Useful before sharing a new screenshot if old ones are still live.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "extend",
        "description": "Extend the TTL of a served image by N minutes from now.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "token": {"type": "string"},
                "minutes": {"type": "number", "default": 60},
            },
            "required": ["token"],
            "additionalProperties": False,
        },
    },
]


def tool_serve_image(args: Dict[str, Any]) -> Dict[str, Any]:
    path = args.get("path", "")
    minutes = float(args.get("minutes", 120))
    if not path:
        return {"isError": True, "content": [{"type": "text", "text": "path is required"}]}
    abs_path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isfile(abs_path):
        return {"isError": True, "content": [{"type": "text", "text": f"file not found: {abs_path}"}]}
    err = ensure_daemon()
    if err:
        return {"isError": True, "content": [{"type": "text", "text": err}]}
    res = http_call("POST", "/control/register", {"path": abs_path, "minutes": minutes})
    if "error" in res:
        return {"isError": True, "content": [{"type": "text", "text": json.dumps(res)}]}
    summary = (
        f"Serving {res['filename']}\n"
        f"URL:     {res['url']}\n"
        f"Token:   {res['token']}\n"
        f"Expires: {fmt_expiry(res['expires_at'])} (in {fmt_remaining(res['expires_at'])})\n"
        f"Size:    {res['size']} bytes"
    )
    return {"content": [{"type": "text", "text": summary}], "structuredContent": res}


def tool_list_served(_args: Dict[str, Any]) -> Dict[str, Any]:
    err = ensure_daemon()
    if err:
        return {"isError": True, "content": [{"type": "text", "text": err}]}
    res = http_call("GET", "/control/list")
    if "error" in res:
        return {"isError": True, "content": [{"type": "text", "text": json.dumps(res)}]}
    items = res.get("items", [])
    if not items:
        return {"content": [{"type": "text", "text": "No images currently being served."}], "structuredContent": res}
    lines = [f"{len(items)} image(s) being served:"]
    for it in items:
        lines.append(
            f"  • {it['filename']}  ({fmt_remaining(it['expires_at'])} left, {it['hits']} hits)"
            f"\n    {it['url']}\n    token: {it['token']}"
        )
    return {"content": [{"type": "text", "text": "\n".join(lines)}], "structuredContent": res}


def tool_revoke(args: Dict[str, Any]) -> Dict[str, Any]:
    token = args.get("token", "")
    if not token:
        return {"isError": True, "content": [{"type": "text", "text": "token is required"}]}
    err = ensure_daemon()
    if err:
        return {"isError": True, "content": [{"type": "text", "text": err}]}
    res = http_call("POST", "/control/revoke", {"token": token})
    msg = f"Revoked token {token}" if res.get("revoked") else f"Token {token} not found"
    return {"content": [{"type": "text", "text": msg}], "structuredContent": res}


def tool_purge_all(_args: Dict[str, Any]) -> Dict[str, Any]:
    err = ensure_daemon()
    if err:
        return {"isError": True, "content": [{"type": "text", "text": err}]}
    res = http_call("POST", "/control/purge")
    return {"content": [{"type": "text", "text": f"Purged {res.get('removed', 0)} image(s)."}], "structuredContent": res}


def tool_extend(args: Dict[str, Any]) -> Dict[str, Any]:
    token = args.get("token", "")
    minutes = float(args.get("minutes", 60))
    if not token:
        return {"isError": True, "content": [{"type": "text", "text": "token is required"}]}
    err = ensure_daemon()
    if err:
        return {"isError": True, "content": [{"type": "text", "text": err}]}
    res = http_call("POST", "/control/extend", {"token": token, "minutes": minutes})
    if "error" in res:
        return {"isError": True, "content": [{"type": "text", "text": json.dumps(res)}]}
    msg = f"Extended by {minutes:g}m. New expiry: {fmt_expiry(res['expires_at'])} (in {fmt_remaining(res['expires_at'])})"
    return {"content": [{"type": "text", "text": msg}], "structuredContent": res}


TOOL_HANDLERS = {
    "serve_image": tool_serve_image,
    "list_served": tool_list_served,
    "revoke": tool_revoke,
    "purge_all": tool_purge_all,
    "extend": tool_extend,
}


# ── JSON-RPC plumbing ────────────────────────────────────────────────────────

def write_frame(obj: Dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def reply(req_id: Any, result: Any) -> None:
    write_frame({"jsonrpc": "2.0", "id": req_id, "result": result})


def error_reply(req_id: Any, code: int, message: str) -> None:
    write_frame({"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}})


def handle_initialize(req_id: Any, params: Dict[str, Any]) -> None:
    client_proto = params.get("protocolVersion", "2025-06-18")
    proto = client_proto if client_proto in PROTOCOL_VERSIONS else "2025-06-18"
    reply(req_id, {
        "protocolVersion": proto,
        "capabilities": {"tools": {}},
        "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
    })


def handle_tools_list(req_id: Any, _params: Dict[str, Any]) -> None:
    reply(req_id, {"tools": TOOLS})


def handle_tools_call(req_id: Any, params: Dict[str, Any]) -> None:
    name = params.get("name", "")
    args = params.get("arguments", {}) or {}
    handler = TOOL_HANDLERS.get(name)
    if not handler:
        reply(req_id, {"isError": True, "content": [{"type": "text", "text": f"unknown tool: {name}"}]})
        return
    try:
        result = handler(args)
    except Exception as e:
        log(f"tool {name} raised: {e!r}")
        reply(req_id, {"isError": True, "content": [{"type": "text", "text": f"internal error: {e}"}]})
        return
    reply(req_id, result)


METHODS = {
    "initialize": handle_initialize,
    "tools/list": handle_tools_list,
    "tools/call": handle_tools_call,
}


def main() -> None:
    log(f"starting (pid={os.getpid()})")
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as e:
            log(f"bad json: {e}; line={line[:200]!r}")
            continue
        method = msg.get("method", "")
        req_id = msg.get("id")
        params = msg.get("params") or {}
        if req_id is None:
            # Notification — no response.
            if method == "notifications/initialized":
                log("client initialized")
            elif method == "notifications/cancelled":
                pass
            else:
                log(f"unhandled notification: {method}")
            continue
        handler = METHODS.get(method)
        if not handler:
            error_reply(req_id, -32601, f"method not found: {method}")
            continue
        try:
            handler(req_id, params)
        except Exception as e:
            log(f"handler {method} raised: {e!r}")
            error_reply(req_id, -32603, f"internal error: {e}")
    log("stdin closed; exiting")


if __name__ == "__main__":
    main()

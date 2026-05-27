#!/usr/bin/env python3
"""Multi-image HTTP daemon for serve-image.

Serves registered images at /<token>/<filename> on a fixed port (default 7890),
bound to 0.0.0.0 so Tailscale peers can reach them. Each image has its own TTL;
expired entries are swept by a background thread.

A localhost-only control plane under /control/* lets clients register, list,
revoke, extend, and purge images. The MCP server and the serve.sh shim both
talk to this control plane via HTTP on 127.0.0.1.

Pure stdlib. Logs to stderr. PID file at ~/.cache/serve-image/server.pid.
"""

from __future__ import annotations

import http.server
import json
import mimetypes
import os
import re
import secrets
import socketserver
import subprocess
import sys
import threading
import time
import urllib.parse
from typing import Any, Dict, Optional, Tuple

mimetypes.add_type("image/webp", ".webp")
mimetypes.add_type("image/svg+xml", ".svg")
mimetypes.add_type("image/png", ".png")
mimetypes.add_type("image/jpeg", ".jpg")
mimetypes.add_type("image/jpeg", ".jpeg")
mimetypes.add_type("image/gif", ".gif")

PORT = int(os.environ.get("SERVE_IMAGE_PORT", "7890"))
CACHE_DIR = os.path.expanduser("~/.cache/serve-image")
PID_FILE = os.path.join(CACHE_DIR, "server.pid")

START_TIME = time.time()


class Registry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: Dict[str, Dict[str, Any]] = {}

    def register(self, path: str, minutes: float) -> Dict[str, Any]:
        if not os.path.isfile(path):
            raise FileNotFoundError(path)
        with open(path, "rb") as f:
            data = f.read()
        filename = os.path.basename(path)
        mime, _ = mimetypes.guess_type(path)
        if not mime:
            mime = "application/octet-stream"
        token = secrets.token_urlsafe(8)
        now = time.time()
        item = {
            "token": token,
            "path": path,
            "filename": filename,
            "mime": mime,
            "data": data,
            "size": len(data),
            "registered_at": now,
            "expires_at": now + minutes * 60.0,
            "hits": 0,
        }
        with self._lock:
            self._items[token] = item
        return self._public(item)

    def get(self, token: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            item = self._items.get(token)
            if not item:
                return None
            if item["expires_at"] < time.time():
                self._items.pop(token, None)
                return None
            return item

    def touch(self, token: str) -> None:
        with self._lock:
            item = self._items.get(token)
            if item:
                item["hits"] += 1

    def list_all(self) -> list:
        now = time.time()
        with self._lock:
            return [self._public(it) for it in self._items.values() if it["expires_at"] >= now]

    def revoke(self, token: str) -> bool:
        with self._lock:
            return self._items.pop(token, None) is not None

    def purge(self) -> int:
        with self._lock:
            n = len(self._items)
            self._items.clear()
            return n

    def extend(self, token: str, minutes: float) -> Optional[Dict[str, Any]]:
        with self._lock:
            item = self._items.get(token)
            if not item:
                return None
            item["expires_at"] = max(item["expires_at"], time.time()) + minutes * 60.0
            return self._public(item)

    def sweep(self) -> int:
        now = time.time()
        with self._lock:
            expired = [t for t, it in self._items.items() if it["expires_at"] < now]
            for t in expired:
                self._items.pop(t, None)
            return len(expired)

    @staticmethod
    def _public(item: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "token": item["token"],
            "filename": item["filename"],
            "path": item["path"],
            "size": item["size"],
            "registered_at": item["registered_at"],
            "expires_at": item["expires_at"],
            "hits": item["hits"],
        }


REGISTRY = Registry()


def _tailscale_ip() -> Optional[str]:
    cli = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"
    if os.access(cli, os.X_OK):
        try:
            out = subprocess.check_output([cli, "ip", "-4"], stderr=subprocess.DEVNULL, timeout=2)
            ip = out.decode().strip().splitlines()[0].strip()
            if ip:
                return ip
        except Exception:
            pass
    try:
        out = subprocess.check_output(["ifconfig"], stderr=subprocess.DEVNULL, timeout=2).decode()
        m = re.search(r"inet (100\.\d+\.\d+\.\d+)", out)
        if m:
            return m.group(1)
    except Exception:
        pass
    return None


def _log(msg: str) -> None:
    print(f"[serve-image-daemon] {msg}", file=sys.stderr, flush=True)


def _url_for(token: str, filename: str) -> str:
    ip = _tailscale_ip() or "127.0.0.1"
    return f"http://{ip}:{PORT}/{token}/{urllib.parse.quote(filename)}"


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "serve-image/2.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        _log(f"{self.address_string()} - " + (fmt % args))

    def _is_localhost(self) -> bool:
        host = self.client_address[0]
        return host in ("127.0.0.1", "::1", "localhost")

    def _send_json(self, status: int, body: Any) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_404(self) -> None:
        self._send_json(404, {"error": "not_found"})

    def _read_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length > 0 else b"{}"
        return json.loads(raw.decode("utf-8") or "{}")

    def _parse(self) -> Tuple[str, str]:
        parsed = urllib.parse.urlparse(self.path)
        return parsed.path, parsed.query

    def _serve_image(self, token: str, filename: str, body: bool) -> None:
        item = REGISTRY.get(token)
        if not item or item["filename"] != filename:
            self._send_404()
            return
        REGISTRY.touch(token)
        data = item["data"]
        self.send_response(200)
        self.send_header("Content-Type", item["mime"])
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Disposition", f'inline; filename="{filename}"')
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        if body:
            self.wfile.write(data)

    def _dispatch_control_get(self, path: str) -> None:
        if not self._is_localhost():
            self._send_json(403, {"error": "forbidden"})
            return
        if path == "/control/health":
            self._send_json(200, {
                "ok": True,
                "pid": os.getpid(),
                "uptime_s": int(time.time() - START_TIME),
                "port": PORT,
                "count": len(REGISTRY.list_all()),
            })
        elif path == "/control/list":
            items = REGISTRY.list_all()
            for it in items:
                it["url"] = _url_for(it["token"], it["filename"])
            self._send_json(200, {"items": items, "tailscale_ip": _tailscale_ip(), "port": PORT})
        else:
            self._send_404()

    def _dispatch_control_post(self, path: str) -> None:
        if not self._is_localhost():
            self._send_json(403, {"error": "forbidden"})
            return
        try:
            body = self._read_json()
        except json.JSONDecodeError:
            self._send_json(400, {"error": "bad_json"})
            return
        if path == "/control/register":
            try:
                img_path = body["path"]
                minutes = float(body.get("minutes", 120))
            except (KeyError, TypeError, ValueError):
                self._send_json(400, {"error": "bad_request"})
                return
            try:
                pub = REGISTRY.register(img_path, minutes)
            except FileNotFoundError:
                self._send_json(404, {"error": "file_not_found", "path": img_path})
                return
            pub["url"] = _url_for(pub["token"], pub["filename"])
            pub["tailscale_ip"] = _tailscale_ip()
            self._send_json(200, pub)
        elif path == "/control/revoke":
            token = body.get("token", "")
            ok = REGISTRY.revoke(token)
            self._send_json(200, {"revoked": ok, "token": token})
        elif path == "/control/purge":
            n = REGISTRY.purge()
            self._send_json(200, {"removed": n})
        elif path == "/control/extend":
            token = body.get("token", "")
            try:
                minutes = float(body.get("minutes", 60))
            except (TypeError, ValueError):
                self._send_json(400, {"error": "bad_minutes"})
                return
            pub = REGISTRY.extend(token, minutes)
            if not pub:
                self._send_json(404, {"error": "not_found", "token": token})
                return
            pub["url"] = _url_for(pub["token"], pub["filename"])
            self._send_json(200, pub)
        else:
            self._send_404()

    def do_GET(self) -> None:
        path, _ = self._parse()
        if path.startswith("/control/"):
            self._dispatch_control_get(path)
            return
        parts = path.lstrip("/").split("/", 1)
        if len(parts) == 2 and parts[0] and parts[1]:
            token = parts[0]
            filename = urllib.parse.unquote(parts[1])
            self._serve_image(token, filename, body=True)
        else:
            self._send_404()

    def do_HEAD(self) -> None:
        path, _ = self._parse()
        if path.startswith("/control/"):
            self._send_404()
            return
        parts = path.lstrip("/").split("/", 1)
        if len(parts) == 2 and parts[0] and parts[1]:
            token = parts[0]
            filename = urllib.parse.unquote(parts[1])
            self._serve_image(token, filename, body=False)
        else:
            self._send_404()

    def do_POST(self) -> None:
        path, _ = self._parse()
        if path.startswith("/control/"):
            self._dispatch_control_post(path)
        else:
            self._send_404()


class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def _write_pid() -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))


def _sweep_loop() -> None:
    while True:
        time.sleep(30)
        n = REGISTRY.sweep()
        if n:
            _log(f"swept {n} expired entries")


def main() -> None:
    _write_pid()
    sweeper = threading.Thread(target=_sweep_loop, daemon=True)
    sweeper.start()
    httpd = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    _log(f"daemon listening on 0.0.0.0:{PORT} (pid={os.getpid()})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        _log("shutting down")


if __name__ == "__main__":
    main()

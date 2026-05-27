#!/usr/bin/env python3
"""Single-file HTTP server that serves one image file on a fixed port, then exits."""

import http.server
import mimetypes
import os
import sys
import threading

# Register MIME types that Python 3.9 on macOS may miss
mimetypes.add_type("image/webp", ".webp")
mimetypes.add_type("image/svg+xml", ".svg")
mimetypes.add_type("image/png", ".png")
mimetypes.add_type("image/jpeg", ".jpg")
mimetypes.add_type("image/jpeg", ".jpeg")
mimetypes.add_type("image/gif", ".gif")

# Parse args
if len(sys.argv) < 3:
    print("Usage: server.py <image-path> <port> [minutes]", file=sys.stderr)
    sys.exit(1)

IMAGE_PATH = sys.argv[1]
PORT = int(sys.argv[2])
MINUTES = float(sys.argv[3]) if len(sys.argv) > 3 else 30.0

if not os.path.isfile(IMAGE_PATH):
    print(f"Error: file not found: {IMAGE_PATH}", file=sys.stderr)
    sys.exit(1)

FILENAME = os.path.basename(IMAGE_PATH)
MIME_TYPE, _ = mimetypes.guess_type(IMAGE_PATH)
if not MIME_TYPE:
    MIME_TYPE = "application/octet-stream"

with open(IMAGE_PATH, "rb") as f:
    IMAGE_DATA = f.read()


class ImageHandler(http.server.BaseHTTPRequestHandler):
    def _send_headers(self):
        self.send_response(200)
        self.send_header("Content-Type", MIME_TYPE)
        self.send_header("Content-Length", str(len(IMAGE_DATA)))
        self.send_header("Content-Disposition", f'inline; filename="{FILENAME}"')
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

    def do_HEAD(self):
        self._send_headers()

    def do_GET(self):
        self._send_headers()
        self.wfile.write(IMAGE_DATA)

    def log_message(self, format, *args):
        print(f"[serve-image] {self.address_string()} - " + format % args)


def shutdown_fn():
    print("[serve-image] Auto-shutdown timer expired.", flush=True)
    os._exit(0)


timer = threading.Timer(MINUTES * 60, shutdown_fn)
timer.daemon = True
timer.start()

http.server.HTTPServer.allow_reuse_address = True
server = http.server.HTTPServer(("0.0.0.0", PORT), ImageHandler)
print(f"[serve-image] Serving {FILENAME} on port {PORT} for {MINUTES:.0f} min", flush=True)

try:
    server.serve_forever()
except KeyboardInterrupt:
    print("[serve-image] Stopped.", flush=True)

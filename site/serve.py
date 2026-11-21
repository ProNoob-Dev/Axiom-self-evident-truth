#!/usr/bin/env python3
"""Serve the Axiom console and proxy /api/* to the FastAPI backend.

Usage: python3 site/serve.py [--port 8130]  (backend must run on 8125:
  uvicorn app.main:app --host 127.0.0.1 --port 8125)
The proxy exists because the backend has no CORS middleware; serving the
console from the same origin keeps every fetch same-origin. stdlib only.
"""
import argparse
import json
import mimetypes
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = "http://127.0.0.1:8125"


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(ROOT), **kw)

    def log_message(self, *a):  # keep the console quiet
        pass

    def _proxy(self):
        path = self.path[len("/api"):] or "/"
        url = BACKEND + path
        body = None
        if self.command in ("POST", "PUT", "PATCH"):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else None
        req = urllib.request.Request(url, data=body, method=self.command)
        if self.headers.get("Content-Type"):
            req.add_header("Content-Type", self.headers["Content-Type"])
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
                self.send_response(resp.status)
                self.send_header("Content-Type", resp.headers.get("Content-Type", "application/json"))
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        except urllib.error.HTTPError as e:
            data = e.read()
            self.send_response(e.code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except OSError as e:
            data = json.dumps({"detail": f"backend unreachable: {e}"}).encode()
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    def do_GET(self):
        if self.path == "/api" or self.path.startswith("/api/"):
            self._proxy()
        else:
            super().do_GET()

    def do_POST(self):
        if self.path == "/api" or self.path.startswith("/api/"):
            self._proxy()
        else:
            self.send_error(404)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8130)
    args = p.parse_args()
    mimetypes.add_type("text/javascript", ".js")
    print(f"Axiom console: http://127.0.0.1:{args.port}/  (API proxied to {BACKEND})")
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()

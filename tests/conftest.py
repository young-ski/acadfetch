from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
BLOCK = (FIXTURES / "figshare_403_block.html").read_bytes()
FORBIDDEN = (FIXTURES / "figshare_403_forbidden.json").read_bytes()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # silence
        pass

    def _refuse(self, body: bytes, content_type: str) -> None:
        self.send_response(403)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self.wfile.flush()

    def do_GET(self):  # noqa: N802
        cfg = self.server.cfg
        path = self.path.split("?")[0]
        cfg["hits"][path] = cfg["hits"].get(path, 0) + 1
        cfg["ranges"].append(self.headers.get("Range"))
        if cfg["answers_416"] and self.headers.get("Range"):
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{len(cfg['files'].get(path, b''))}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path in cfg["forbidden"]:
            self._refuse(FORBIDDEN, "application/json")
            return
        if path in cfg["always_500"]:
            self.send_response(500)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if cfg["blocked"].get(path, 0) > 0:
            cfg["blocked"][path] -= 1
            self._refuse(BLOCK, "text/html")
            return
        if path == "/stall":
            self.send_response(200)
            self.send_header("Content-Length", str(10_000))
            self.end_headers()
            self.wfile.write(b"z" * 100)
            self.wfile.flush()
            __import__("time").sleep(3)
            return
        if path not in cfg["files"]:
            self.send_error(404)
            return
        payload = cfg["files"][path]

        start = 0
        wanted = self.headers.get("Range")
        if wanted and not cfg["ignore_range"]:
            if_range = self.headers.get("If-Range")
            if if_range is None or if_range == cfg["etag"]:
                start = int(wanted.split("=")[1].split("-")[0])

        if path in cfg["lies_about_range"]:
            # Answers 206 with the whole body and a Content-Range from zero,
            # which is what a server ignoring If-Range looks like.
            body = payload
            self.send_response(206)
            self.send_header("Content-Range", f"bytes 0-{len(payload) - 1}/{len(payload)}")
        else:
            body = payload[start:]
            self.send_response(206 if start else 200)
            if start:
                self.send_header(
                    "Content-Range", f"bytes {start}-{len(payload) - 1}/{len(payload)}"
                )
        self.send_header("Content-Type", cfg["types"].get(path, "application/octet-stream"))
        declared = cfg["declares"].get(path, len(body))
        self.send_header("Content-Length", str(declared))
        try:  # a malformed header (e.g. "12, 12") is a case under test
            declared_n: int | None = int(str(declared).strip())
        except ValueError:
            declared_n = None
        self.send_header("ETag", cfg["etag"])
        self.end_headers()
        limit = cfg.get("truncate_at")
        if limit is not None and cfg["hits"][path] <= cfg.get("truncate_times", 1):
            body = body[:limit]
        if declared_n is not None and declared_n > len(body):
            pass  # declared more than it delivers; the client must notice
        # A stall in the middle of the body, longer than the 1 s read poll:
        # the shape of a flaky VPN, and of a slow cellular link.
        stall_after = cfg.get("stall_after")
        if stall_after is not None and cfg["hits"][path] == 1 and stall_after < len(body):
            self.wfile.write(body[:stall_after])
            self.wfile.flush()
            time.sleep(cfg.get("stall_for", 2.0))
            body = body[stall_after:]
        self.wfile.write(body)
        self.wfile.flush()


@pytest.fixture
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    srv.cfg = {
        "files": {},
        "hits": {},
        "etag": '"v1"',
        "ignore_range": False,
        "truncate_at": None,
        "blocked": {},
        "forbidden": set(),
        "always_500": set(),
        "lies_about_range": set(),
        "declares": {},
        "types": {},
        "answers_416": False,
        "ranges": [],
        "stall_after": None,
        "stall_for": 2.0,
    }
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    srv.base = f"http://127.0.0.1:{srv.server_address[1]}"
    yield srv
    srv.shutdown()
    srv.server_close()

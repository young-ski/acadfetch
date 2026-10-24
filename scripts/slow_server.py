"""Slow local file server for eyeballing progress output (2 MiB/s, Range-aware)."""

import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BLOB = bytes(range(256)) * (64 * 1024)  # 16 MiB
RATE = 1024 * 1024


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Length", str(len(BLOB)))
        self.send_header("ETag", '"slow"')
        self.end_headers()

    def do_GET(self):
        start = 0
        rng = self.headers.get("Range")
        if rng:
            start = int(rng.split("=")[1].split("-")[0])
        body = BLOB[start:]
        self.send_response(206 if start else 200)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("ETag", '"slow"')
        self.end_headers()
        step = 64 * 1024
        for i in range(0, len(body), step):
            self.wfile.write(body[i : i + step])
            self.wfile.flush()
            time.sleep(step / RATE)


print("serving on 8792")
ThreadingHTTPServer(("127.0.0.1", 8792), H).serve_forever()

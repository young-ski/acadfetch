"""Measure the disk work the transfer path saves (or does not).

One local download, then the two reads the old code did afterwards —
``verify.matches`` and the manifest's SHA-256 — against the digests the transfer
already produced. Nothing here needs the network.

    python scripts/bench_hash.py [MiB]
"""

import hashlib
import http.server
import socketserver
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from acadfetch import downloader, verify  # noqa: E402
from acadfetch.client import Client  # noqa: E402
from acadfetch.sources.base import File  # noqa: E402

MIB = int(sys.argv[1]) if len(sys.argv) > 1 else 64
BODY = bytes(range(256)) * ((MIB << 20) // 256)


class H(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", str(len(BODY)))
        self.end_headers()
        self.wfile.write(BODY)


srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{srv.server_address[1]}/big.bin"
digest = hashlib.sha256(BODY).hexdigest()

with tempfile.TemporaryDirectory() as tmp:
    out = Path(tmp)
    opts = downloader.Options(out=out)
    file = File(url, "big.bin", len(BODY), f"sha256:{digest}")

    started = time.perf_counter()
    result = downloader.fetch_file(Client(rate=1e6), file, opts, set())
    fetch_s = time.perf_counter() - started
    assert result.state == "downloaded" and result.verified is True, result.error

    started = time.perf_counter()
    verify.matches(out / "big.bin", file.checksum)
    reread_check_s = time.perf_counter() - started

    started = time.perf_counter()
    verify.hash_file(out / "big.bin", "sha256")
    reread_manifest_s = time.perf_counter() - started

size = len(BODY) / (1 << 20)
print(f"file            : {size:.0f} MiB")
print(f"transfer + hash : {fetch_s:.2f} s  ({size / fetch_s:.0f} MiB/s effective)")
print(f"old re-check    : {reread_check_s:.2f} s")
print(f"old re-hash     : {reread_manifest_s:.2f} s")
print(f"reads saved     : 2 full passes, {size * 2:.0f} MiB")
srv.shutdown()

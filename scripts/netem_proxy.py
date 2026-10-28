"""A network-shaping CONNECT proxy: for the E2 profile of the first-success bench.

Stands between acadfetch and a real repository and makes the link behave like a
bad one: throttled, and with one deliberate stall in the middle of the first
body. No admin rights, no driver, works on Windows and POSIX.

    python scripts/netem_proxy.py --port 8899 --rate 1048576 --stall-after 131072 --stall-for 2

then point the tool at it:

    HTTPS_PROXY=http://127.0.0.1:8899 acadfetch fetch 10.5281/zenodo.23612969

The stall is per completed CONNECT tunnel and is exactly what the ``_chunks`` poll
loop is supposed to survive: a socket timeout shorter than the stall poisons the
response, which is the defect this profile exists to catch.
"""

from __future__ import annotations

import argparse
import contextlib
import socket
import socketserver
import sys
import threading
import time

CHUNK = 1 << 15
BUFFER = 1 << 16


class Shape:
    """Per-connection budget: one stall, then a throttle on every write."""

    def __init__(self, rate: int, stall_after: int, stall_for: float) -> None:
        self.rate = rate
        self.stall_after = stall_after
        self.stall_for = stall_for
        self.moved = 0
        self.stalled = False
        self.lock = threading.Lock()

    def wait(self, size: int) -> None:
        if self.rate > 0:
            time.sleep(size / self.rate)
        if self.stalled:
            return
        with self.lock:
            self.moved += size
            if self.moved < self.stall_after:
                return
            self.stalled = True
        if self.stall_for > 0:
            sys.stderr.write(f"  stall {self.stall_for}s after {self.moved} B\n")
            sys.stderr.flush()
            time.sleep(self.stall_for)


def _pump(src: socket.socket, dst: socket.socket, shape: Shape | None) -> None:
    """Copy one direction until EOF or a closed peer."""
    try:
        while True:
            data = src.recv(BUFFER)
            if not data:
                break
            if shape is not None:
                shape.wait(len(data))
            dst.sendall(data)
    except OSError:
        pass
    finally:
        for sock in (src, dst):
            with contextlib.suppress(OSError):
                sock.shutdown(socket.SHUT_RDWR)


class Handler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        client = self.request
        head = b""
        while b"\r\n\r\n" not in head:
            part = client.recv(BUFFER)
            if not part:
                return
            head += part
            if len(head) > 64 << 10:
                return
        request_line, _, rest = head.partition(b"\r\n")
        method, target, _ = request_line.decode("latin-1").split(" ", 2)

        if method.upper() != "CONNECT":
            # Plain http: absolute-form target, forward the request as-is.
            self._plain(client, method, target, head)
            return

        host, _, port = target.rpartition(":")
        upstream_addr = self.server.resolve.get(host, host)  # for a blackholed resolver
        try:
            upstream = socket.create_connection((upstream_addr, int(port)), timeout=30)
        except OSError as exc:
            client.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            sys.stderr.write(f"  upstream {target} failed: {exc}\n")
            return
        client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        client.settimeout(None)
        shape = Shape(
            self.server.shape.rate, self.server.shape.stall_after, self.server.shape.stall_for
        )
        # Shape the direction the bytes actually come from.
        pump = threading.Thread(target=_pump, args=(client, upstream, None), daemon=True)
        pump.start()
        _pump(upstream, client, shape)
        pump.join(timeout=5)

    def _plain(self, client: socket.socket, method: str, target: str, head: bytes) -> None:
        host, _, port = target.split("://", 1)[1].partition("/")[0].rpartition(":")
        if not host:
            host, port = target.split("://", 1)[1].split("/")[0], "80"
        try:
            upstream = socket.create_connection((host, int(port or 80)), timeout=30)
        except OSError:
            client.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            return
        upstream.sendall(head)
        shape = Shape(
            self.server.shape.rate, self.server.shape.stall_after, self.server.shape.stall_for
        )
        pump = threading.Thread(target=_pump, args=(client, upstream, None), daemon=True)
        pump.start()
        _pump(upstream, client, shape)
        pump.join(timeout=5)


class Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8899)
    ap.add_argument("--rate", type=int, default=1 << 20, help="bytes/s downstream, 0 = unthrottled")
    ap.add_argument(
        "--stall-after", type=int, default=1 << 17, help="stall once after N body bytes"
    )
    ap.add_argument("--stall-for", type=float, default=2.0, help="seconds the stall lasts")
    ap.add_argument(
        "--resolve",
        action="append",
        default=[],
        metavar="HOST=IP",
        help="bypass a blackholed resolver for one host, repeatable",
    )
    args = ap.parse_args(argv)

    server = Server(("127.0.0.1", args.port), Handler)
    server.shape = args  # per-connection Shape reads these three fields
    server.resolve = dict(item.split("=", 1) for item in args.resolve)
    pinned = ", ".join(f"{k}->{v}" for k, v in server.resolve.items()) or "system resolver"
    print(
        f"listening on 127.0.0.1:{server.server_address[1]} "
        f"(rate={args.rate} B/s, stall {args.stall_for}s after {args.stall_after} B, {pinned})",
        flush=True,
    )
    with contextlib.suppress(KeyboardInterrupt):
        server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())

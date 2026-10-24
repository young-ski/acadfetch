from __future__ import annotations

import hashlib
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from acadfetch import downloader
from acadfetch.client import Client
from acadfetch.sources.base import File, Record


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # silence
        pass

    def do_GET(self):  # noqa: N802
        cfg = self.server.cfg
        path = self.path.split("?")[0]
        if path not in cfg["files"]:
            self.send_error(404)
            return
        payload = cfg["files"][path]
        cfg["hits"][path] = cfg["hits"].get(path, 0) + 1

        start = 0
        wanted = self.headers.get("Range")
        if wanted and not cfg["ignore_range"]:
            if_range = self.headers.get("If-Range")
            if if_range is None or if_range == cfg["etag"]:
                start = int(wanted.split("=")[1].split("-")[0])

        body = payload[start:]
        self.send_response(206 if start else 200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("ETag", cfg["etag"])
        self.end_headers()
        limit = cfg.get("truncate_at")
        if limit is not None and cfg["hits"][path] <= cfg.get("truncate_times", 1):
            body = body[:limit]
        self.wfile.write(body)
        self.wfile.flush()


@pytest.fixture
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    srv.cfg = {"files": {}, "hits": {}, "etag": '"v1"', "ignore_range": False, "truncate_at": None}
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    srv.base = f"http://127.0.0.1:{srv.server_address[1]}"
    yield srv
    srv.shutdown()
    srv.server_close()


def opts(tmp_path: Path, **kw) -> downloader.Options:
    return downloader.Options(out=tmp_path, concurrency=1, retries=2, **kw)


def make_file(server, path="/a.bin", payload=b"hello world" * 100, **kw):
    server.cfg["files"][path] = payload
    return File(
        url=server.base + path,
        name=path.lstrip("/"),
        size=len(payload),
        checksum=kw.get("checksum"),
    )


def test_plain_download(server, tmp_path):
    payload = b"hello world" * 100
    f = make_file(server, payload=payload, checksum="sha256:" + hashlib.sha256(payload).hexdigest())
    res = downloader.fetch_file(Client(rate=100, retries=1), f, opts(tmp_path), set())
    assert res.state == "downloaded"
    assert res.verified is True
    assert (tmp_path / "a.bin").read_bytes() == payload
    assert not (tmp_path / "a.bin.part").exists()


def test_resume_from_part(server, tmp_path):
    payload = b"0123456789" * 50
    f = make_file(server, payload=payload, checksum="sha256:" + hashlib.sha256(payload).hexdigest())
    part = tmp_path / "a.bin.part"
    part.write_bytes(payload[:120])
    (tmp_path / "a.bin.part.meta.json").write_text(
        f'{{"url": "{f.url}", "etag": "\\"v1\\""}}', "utf-8"
    )
    res = downloader.fetch_file(Client(rate=100, retries=1), f, opts(tmp_path), set())
    assert res.state == "downloaded"
    assert res.resumed_from == 120
    assert (tmp_path / "a.bin").read_bytes() == payload


def test_range_ignored_falls_back_to_full(server, tmp_path):
    payload = b"abcdef" * 40
    server.cfg["ignore_range"] = True
    f = make_file(server, payload=payload, checksum="sha256:" + hashlib.sha256(payload).hexdigest())
    (tmp_path / "a.bin.part").write_bytes(b"GARBAGE")
    (tmp_path / "a.bin.part.meta.json").write_text(
        f'{{"url": "{f.url}", "etag": "\\"v0\\""}}', "utf-8"
    )
    res = downloader.fetch_file(Client(rate=100, retries=1), f, opts(tmp_path), set())
    assert res.state == "downloaded"
    assert (tmp_path / "a.bin").read_bytes() == payload


def test_checksum_mismatch_retries_then_fails(server, tmp_path):
    f = make_file(server, checksum="sha256:" + "0" * 64)
    res = downloader.fetch_file(Client(rate=100, retries=1), f, opts(tmp_path), set())
    assert res.state == "failed"
    assert "mismatch" in res.error
    assert not (tmp_path / "a.bin.part").exists()


def test_skips_already_complete(server, tmp_path):
    payload = b"x" * 10
    f = make_file(server, payload=payload, checksum="sha256:" + hashlib.sha256(payload).hexdigest())
    (tmp_path / "a.bin").write_bytes(payload)
    res = downloader.fetch_file(Client(rate=100, retries=1), f, opts(tmp_path), set())
    assert res.state == "skipped"
    assert server.cfg["hits"].get("/a.bin") is None


def test_dry_run(server, tmp_path):
    f = make_file(server)
    res = downloader.fetch_file(Client(rate=100, retries=1), f, opts(tmp_path, dry_run=True), set())
    assert res.state == "skipped"
    assert not (tmp_path / "a.bin").exists()
    assert server.cfg["hits"] == {}


def test_max_bytes_aborts(server, tmp_path):
    f = make_file(server, payload=b"z" * 5000)
    res = downloader.fetch_file(
        Client(rate=100, retries=1), f, opts(tmp_path, max_bytes=1000), set()
    )
    assert res.state == "failed"
    assert "max-size" in res.error


def test_short_read_is_retried(server, tmp_path):
    payload = b"q" * 3000
    server.cfg["truncate_at"] = 700
    f = make_file(server, payload=payload, checksum="sha256:" + hashlib.sha256(payload).hexdigest())
    res = downloader.fetch_file(Client(rate=100, retries=1), f, opts(tmp_path), set())
    assert res.state == "downloaded"
    assert (tmp_path / "a.bin").read_bytes() == payload


def test_fetch_record_parallel(server, tmp_path):
    a, b = b"a" * 900, b"b" * 900
    server.cfg["files"]["/a.bin"] = a
    server.cfg["files"]["/b.bin"] = b
    record = Record(
        source="direct",
        target="test",
        files=[
            File(server.base + "/a.bin", "a.bin", 900),
            File(server.base + "/b.bin", "b.bin", 900),
        ],
    )
    o = downloader.Options(out=tmp_path, concurrency=2, retries=1)
    results = downloader.fetch_record(record, o, Client(rate=100, retries=1))
    assert [r.state for r in results] == ["downloaded", "downloaded"]
    assert (tmp_path / "a.bin").read_bytes() == a


def test_duplicate_names_do_not_clobber(server, tmp_path):
    server.cfg["files"]["/a.bin"] = b"one"
    server.cfg["files"]["/dup/a.bin"] = b"two"
    record = Record(
        source="direct",
        target="test",
        files=[File(server.base + "/a.bin", "a.bin"), File(server.base + "/dup/a.bin", "a.bin")],
    )
    results = downloader.fetch_record(
        record, downloader.Options(out=tmp_path), Client(rate=100, retries=1)
    )
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a (2).bin", "a.bin"]
    assert [r.state for r in results] == ["downloaded", "downloaded"]


class Recorder:
    def __init__(self):
        self.calls = []

    def start(self, name, size):
        self.calls.append(("start", name, size))

    def stop(self, name):
        self.calls.append(("stop", name))

    def advance(self, n):
        self.calls.append(("advance", n))


def test_progress_hooks_bracket_the_transfer(server, tmp_path):
    payload = b"p" * 200_000
    f = make_file(server, payload=payload)
    rec = Recorder()
    o = opts(tmp_path)
    o.progress = rec
    res = downloader.fetch_file(Client(rate=100, retries=1), f, o, set())
    assert res.state == "downloaded"
    kinds = [c[0] for c in rec.calls]
    assert kinds[0] == "start" and kinds[-1] == "stop"
    assert sum(c[1] for c in rec.calls if c[0] == "advance") == len(payload)
    assert rec.calls[0][2] == len(payload)


def test_progress_stops_even_when_transfer_fails(server, tmp_path):
    f = make_file(server, payload=b"z" * 5000, checksum="sha256:" + "0" * 64)
    rec = Recorder()
    o = opts(tmp_path)
    o.progress = rec
    res = downloader.fetch_file(Client(rate=100, retries=1), f, o, set())
    assert res.state == "failed"
    assert rec.calls[-1] == ("stop", "a.bin")


def test_skipped_file_never_starts_progress(server, tmp_path):
    payload = b"x" * 10
    f = make_file(server, payload=payload, checksum="sha256:" + hashlib.sha256(payload).hexdigest())
    (tmp_path / "a.bin").write_bytes(payload)
    rec = Recorder()
    o = opts(tmp_path)
    o.progress = rec
    downloader.fetch_file(Client(rate=100, retries=1), f, o, set())
    assert rec.calls == []

from __future__ import annotations

import hashlib
import signal
import threading
from pathlib import Path

from acadfetch import downloader
from acadfetch.client import Client
from acadfetch.sources.base import File, Record

FIXTURES = Path(__file__).parent / "fixtures"
# A real figshare file and the md5 its payload publishes for it.
REAL = (FIXTURES / "figshare_file.pdf").read_bytes()
REAL_MD5 = "md5:bbde85208529e663141a0e8cb6fa9c4a"
REAL_NAME = "3_Summary of Findings Across Studies_20261024_v2.pdf"


def opts(tmp_path: Path, **kw) -> downloader.Options:
    kw.setdefault("retries", 2)
    return downloader.Options(out=tmp_path, concurrency=1, **kw)


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


# -- 403s: block pages are transient, permission errors are not -----------


def test_block_page_403_is_retried(server, tmp_path):
    payload = b"w" * 500
    f = make_file(server, payload=payload, checksum="sha256:" + hashlib.sha256(payload).hexdigest())
    server.cfg["blocked"]["/a.bin"] = 2
    res = downloader.fetch_file(Client(rate=1000, retries=1), f, opts(tmp_path), set())
    assert res.state == "downloaded"
    assert res.verified is True
    assert (tmp_path / "a.bin").read_bytes() == payload


def test_block_page_403_keeps_the_partial_file(server, tmp_path):
    payload = b"0123456789" * 50
    f = make_file(server, payload=payload, checksum="sha256:" + hashlib.sha256(payload).hexdigest())
    (tmp_path / "a.bin.part").write_bytes(payload[:120])
    (tmp_path / "a.bin.part.meta.json").write_text(
        f'{{"url": "{f.url}", "etag": "\\"v1\\""}}', "utf-8"
    )
    server.cfg["blocked"]["/a.bin"] = 1
    res = downloader.fetch_file(Client(rate=1000, retries=1), f, opts(tmp_path), set())
    assert res.state == "downloaded"
    assert res.resumed_from == 120
    assert (tmp_path / "a.bin").read_bytes() == payload


def test_block_page_403_is_capped(server, tmp_path):
    """A permanently blocking host must not be hammered forever."""
    f = make_file(server, payload=b"w" * 500)
    server.cfg["blocked"]["/a.bin"] = 99
    res = downloader.fetch_file(Client(rate=1000, retries=1), f, opts(tmp_path), set())
    assert res.state == "failed"
    assert "403" in res.error
    assert 3 <= server.cfg["hits"]["/a.bin"] <= 6
    assert not (tmp_path / "a.bin.part").exists()


def test_permission_403_fails_without_retrying(server, tmp_path):
    f = make_file(server, payload=b"w" * 500)
    server.cfg["forbidden"].add("/a.bin")
    res = downloader.fetch_file(Client(rate=1000, retries=1), f, opts(tmp_path), set())
    assert res.state == "failed"
    assert "403" in res.error
    assert server.cfg["hits"]["/a.bin"] == 1


def test_block_retries_zero_also_stops_the_transfer_retries(server, tmp_path):
    """--retry-403 0 must not be re-added by the download layer."""
    f = make_file(server, payload=b"w" * 500)
    server.cfg["blocked"]["/a.bin"] = 99
    client = Client(rate=1000, retries=1, block_retries=0)
    res = downloader.fetch_file(client, f, opts(tmp_path), set())
    assert res.state == "failed"
    assert server.cfg["hits"]["/a.bin"] == 1


def test_fallback_url_is_used_when_the_first_is_refused(server, tmp_path):
    payload = b"fallback bytes" * 40
    server.cfg["files"]["/alt.bin"] = payload
    server.cfg["forbidden"].add("/a.bin")
    f = File(
        url=server.base + "/a.bin",
        name="a.bin",
        size=len(payload),
        checksum="sha256:" + hashlib.sha256(payload).hexdigest(),
        fallbacks=[server.base + "/alt.bin"],
    )
    res = downloader.fetch_file(Client(rate=1000, retries=1), f, opts(tmp_path), set())
    assert res.state == "downloaded"
    assert res.verified is True
    assert (tmp_path / "a.bin").read_bytes() == payload
    assert server.cfg["hits"]["/alt.bin"] == 1


def test_fallback_restarts_instead_of_appending(server, tmp_path):
    payload = b"F" * 400
    server.cfg["files"]["/alt.bin"] = payload
    server.cfg["forbidden"].add("/a.bin")
    (tmp_path / "a.bin.part").write_bytes(b"stale")
    (tmp_path / "a.bin.part.meta.json").write_text(f'{{"url": "{server.base}/a.bin"}}', "utf-8")
    f = File(
        url=server.base + "/a.bin",
        name="a.bin",
        size=len(payload),
        checksum="sha256:" + hashlib.sha256(payload).hexdigest(),
        fallbacks=[server.base + "/alt.bin"],
    )
    res = downloader.fetch_file(Client(rate=1000, retries=1), f, opts(tmp_path), set())
    assert res.state == "downloaded"
    assert (tmp_path / "a.bin").read_bytes() == payload


# -- a real file against the checksum the repository published ------------


def test_real_bytes_are_verified_against_the_published_md5(server, tmp_path):
    server.cfg["files"]["/70600233"] = REAL
    f = File(server.base + "/70600233", REAL_NAME, len(REAL), REAL_MD5)
    res = downloader.fetch_file(Client(rate=1000, retries=1), f, opts(tmp_path), set())
    assert res.state == "downloaded"
    assert res.verified is True
    assert (tmp_path / REAL_NAME).read_bytes() == REAL


def test_one_flipped_byte_against_the_real_md5_fails(server, tmp_path):
    tampered = bytearray(REAL)
    tampered[-1] ^= 0xFF
    server.cfg["files"]["/70600233"] = bytes(tampered)
    f = File(server.base + "/70600233", REAL_NAME, len(REAL), REAL_MD5)
    res = downloader.fetch_file(Client(rate=1000, retries=1), f, opts(tmp_path), set())
    assert res.state == "failed"
    assert "mismatch" in res.error
    assert not (tmp_path / REAL_NAME).exists()


# -- integrity: lying headers, bad offsets, bounded retries ----------------


def test_malformed_content_length_is_a_failure_not_a_crash(server, tmp_path):
    f = make_file(server, payload=b"hello world", size=None)
    server.cfg["declares"]["/a.bin"] = "12, 12"
    res = downloader.fetch_file(Client(rate=1000, retries=1), f, opts(tmp_path), set())
    assert res.state == "failed"
    assert "Content-Length" in res.error


def test_content_length_disagreeing_with_the_published_size_fails(server, tmp_path):
    payload = b"y" * 500
    f = make_file(server, payload=payload)
    server.cfg["declares"]["/a.bin"] = 10
    res = downloader.fetch_file(Client(rate=1000, retries=1), f, opts(tmp_path), set())
    assert res.state == "failed"
    assert "disagrees with the published size" in res.error


def test_a_206_from_the_wrong_offset_restarts_instead_of_splicing(server, tmp_path):
    payload = b"0123456789" * 40
    f = make_file(server, payload=payload, checksum="sha256:" + hashlib.sha256(payload).hexdigest())
    (tmp_path / "a.bin.part").write_bytes(payload[:100])
    (tmp_path / "a.bin.part.meta.json").write_text(
        f'{{"url": "{f.url}", "etag": "\\"v1\\""}}', "utf-8"
    )
    server.cfg["lies_about_range"].add("/a.bin")
    res = downloader.fetch_file(Client(rate=1000, retries=1), f, opts(tmp_path), set())
    assert res.state == "downloaded"
    assert (tmp_path / "a.bin").read_bytes() == payload


def test_retries_zero_means_one_attempt(server, tmp_path):
    f = make_file(server, payload=b"z" * 100, checksum=None)
    server.cfg["always_500"].add("/a.bin")
    res = downloader.fetch_file(Client(rate=1000, retries=9), f, opts(tmp_path, retries=0), set())
    assert res.state == "failed"
    assert server.cfg["hits"]["/a.bin"] == 1


def test_a_permanent_500_costs_one_attempt_per_retry_not_a_square(server, tmp_path):
    """The budget belongs to the transfer layer; the client must not multiply it."""
    f = make_file(server, payload=b"z" * 100, checksum=None)
    server.cfg["always_500"].add("/a.bin")
    downloader.fetch_file(Client(rate=1000, retries=9), f, opts(tmp_path, retries=4), set())
    assert server.cfg["hits"]["/a.bin"] == 5


def test_max_size_failure_drops_the_part(server, tmp_path):
    f = make_file(server, payload=b"z" * 5000, size=None)
    res = downloader.fetch_file(
        Client(rate=1000, retries=1), f, opts(tmp_path, max_bytes=1000), set()
    )
    assert res.state == "failed"
    assert not (tmp_path / "a.bin.part").exists()


# -- interrupts: stop promptly, keep the .part, say so --------------------


def _record_with(tmp_path, count=2):
    return Record(
        source="direct",
        target="t",
        files=[File(f"http://example.invalid/{i}.bin", f"{i}.bin", 100) for i in range(count)],
    )


def test_interrupt_is_reported_and_the_part_survives(tmp_path, monkeypatch):
    def interrupting(client, file, part, meta_path, opts):
        part.write_bytes(b"half")
        raise KeyboardInterrupt

    monkeypatch.setattr(downloader, "_transfer", interrupting)
    o = downloader.Options(out=tmp_path, concurrency=1)
    results = downloader.fetch_record(_record_with(tmp_path), o, Client(rate=1000))
    assert o.interrupted is True
    assert [r.error for r in results] == ["interrupted"]
    assert (tmp_path / "0.bin.part").exists()


def test_a_stop_skips_remaining_retries(tmp_path, monkeypatch):
    calls = []

    def failing(client, file, part, meta_path, opts):
        calls.append(1)
        downloader._STOP.set()  # what the interrupt handler does
        raise OSError("socket went away")

    monkeypatch.setattr(downloader, "_transfer", failing)
    o = downloader.Options(out=tmp_path, concurrency=1, retries=5)
    res = downloader.fetch_record(_record_with(tmp_path, 1), o, Client(rate=1000))
    assert res[0].error == "interrupted"
    assert len(calls) == 1  # no sleeping through the rest of the budget


def test_concurrent_interrupt_keeps_completed_results(tmp_path, monkeypatch):
    done = []

    def ok(client, file, part, meta_path, opts):
        done.append(file.name)
        part.write_bytes(b"x" * (file.size or 0))

    monkeypatch.setattr(downloader, "_transfer", ok)
    real = downloader.as_completed

    def one_then_interrupt(futures):
        first = next(iter(futures))
        yield first
        raise KeyboardInterrupt

    monkeypatch.setattr(downloader, "as_completed", one_then_interrupt)
    o = downloader.Options(out=tmp_path, concurrency=2)
    results = downloader.fetch_record(_record_with(tmp_path, 3), o, Client(rate=1000))
    monkeypatch.setattr(downloader, "as_completed", real)
    assert o.interrupted is True
    assert results  # whatever finished is still reported
    assert len(done) >= 1


def test_a_stalled_body_hits_the_idle_budget(server, tmp_path):
    """A silent socket must fail on its own timer, not hang until the OS gives up."""
    f = File(server.base + "/stall", "a.bin", None, None)
    client = Client(rate=1000, retries=0, timeout=0.3)
    res = downloader.fetch_file(client, f, opts(tmp_path, retries=0), set())
    assert res.state == "failed"
    assert "timed out" in res.error.lower()
    # and it says what to do next, not just what went wrong
    assert "rerun to resume" in res.error


def test_survives_a_stall_longer_than_the_read_poll(server, tmp_path):
    """One 1.6 s gap mid-body must cost no retry and no bytes.

    ``socket.SocketIO`` latches its first timeout and refuses every later read,
    so a stall used to end the transfer even though the socket was healthy.
    """
    payload = bytes(range(256)) * 1200  # 307_200 bytes, several chunks
    server.cfg["files"]["/a.bin"] = payload
    server.cfg["stall_after"] = 70_000
    server.cfg["stall_for"] = 1.6
    f = File(server.base + "/a.bin", "a.bin", len(payload))
    res = downloader.fetch_file(Client(rate=1000, retries=0), f, opts(tmp_path, retries=0), set())
    assert res.state == "downloaded", res.error
    assert (tmp_path / "a.bin").read_bytes() == payload
    assert server.cfg["hits"]["/a.bin"] == 1, "a stall must not need a retry"


def test_part_survives_a_spent_retry_budget(server, tmp_path):
    """A failure that already paid for bytes keeps them, and says where they are."""
    server.cfg["files"]["/a.bin"] = b"q" * 5_000
    server.cfg["declares"]["/a.bin"] = 999_999  # a server that over-promises
    server.cfg["truncate_at"] = 10
    f = File(server.base + "/a.bin", "a.bin")  # size unknown, so no early check
    res = downloader.fetch_file(Client(rate=1000, retries=0), f, opts(tmp_path, retries=0), set())
    assert res.state == "failed"
    part = tmp_path / "a.bin.part"
    assert part.read_bytes() == b"q" * 10
    assert res.part == str(part)
    assert res.bytes == 10
    assert not (tmp_path / "a.bin").exists()


def test_part_is_dropped_on_a_checksum_mismatch(server, tmp_path):
    """Bytes that do not match are worthless to resume from, so they go."""
    payload = b"wrong" * 100
    server.cfg["files"]["/a.bin"] = payload
    f = File(server.base + "/a.bin", "a.bin", len(payload), "md5:" + "0" * 32)
    res = downloader.fetch_file(Client(rate=1000, retries=0), f, opts(tmp_path, retries=0), set())
    assert res.state == "failed"
    assert "checksum" in res.error
    assert res.part is None
    assert not (tmp_path / "a.bin.part").exists()
    assert not (tmp_path / "a.bin").exists()


HTML_PAGE = b"<!DOCTYPE html><html><body>403 Forbidden</body></html>"


def test_an_html_body_for_a_pdf_is_refused(server, tmp_path):
    """A WAF block page must not become a .pdf that later runs skip."""
    server.cfg["files"]["/a.pdf"] = HTML_PAGE
    server.cfg["types"]["/a.pdf"] = "text/html; charset=utf-8"
    f = File(server.base + "/a.pdf", "a.pdf", len(HTML_PAGE))
    res = downloader.fetch_file(Client(rate=1000, retries=0), f, opts(tmp_path, retries=0), set())
    assert res.state == "failed"
    assert "text/html" in res.error and "login page" in res.error
    assert res.part is None
    assert not (tmp_path / "a.pdf").exists()
    assert not (tmp_path / "a.pdf.part").exists()


def test_an_html_body_for_an_html_file_is_fine(server, tmp_path):
    server.cfg["files"]["/index.html"] = HTML_PAGE
    server.cfg["types"]["/index.html"] = "text/html"
    f = File(server.base + "/index.html", "index.html", len(HTML_PAGE))
    res = downloader.fetch_file(Client(rate=1000, retries=0), f, opts(tmp_path, retries=0), set())
    assert res.state == "downloaded"
    assert (tmp_path / "index.html").read_bytes() == HTML_PAGE


def test_an_html_body_with_an_unknown_extension_is_fine(server, tmp_path):
    server.cfg["files"]["/payload"] = HTML_PAGE
    server.cfg["types"]["/payload"] = "text/html"
    f = File(server.base + "/payload", "payload", len(HTML_PAGE))
    res = downloader.fetch_file(Client(rate=1000, retries=0), f, opts(tmp_path, retries=0), set())
    assert res.state == "downloaded"


def test_a_416_on_a_complete_part_finishes_the_file(server, tmp_path):
    """The server says "you already have it all"; believe it once, then confirm."""
    import json

    payload = b"y" * 3_000
    server.cfg["files"]["/a.bin"] = payload
    url = server.base + "/a.bin"
    (tmp_path / "a.bin.part").write_bytes(payload)
    (tmp_path / "a.bin.part.meta.json").write_text(
        json.dumps({"url": url, "etag": '"v1"', "size": len(payload)}), "utf-8"
    )
    server.cfg["answers_416"] = True
    f = File(url, "a.bin", len(payload))
    res = downloader.fetch_file(Client(rate=1000, retries=0), f, opts(tmp_path, retries=0), set())
    assert res.state == "downloaded", res.error
    assert (tmp_path / "a.bin").read_bytes() == payload
    assert server.cfg["ranges"] == [f"bytes={len(payload)}-"]
    assert not (tmp_path / "a.bin.part").exists()


def test_a_different_url_restarts_instead_of_appending(server, tmp_path):
    """A sidecar from another URL must not be spliced onto this body."""
    import json

    payload = b"n" * 2_000
    server.cfg["files"]["/a.bin"] = payload
    (tmp_path / "a.bin.part").write_bytes(b"stale")
    (tmp_path / "a.bin.part.meta.json").write_text(
        json.dumps({"url": "http://elsewhere/other", "etag": '"v0"'}), "utf-8"
    )
    f = File(server.base + "/a.bin", "a.bin", len(payload))
    res = downloader.fetch_file(Client(rate=1000, retries=0), f, opts(tmp_path, retries=0), set())
    assert res.state == "downloaded"
    assert (tmp_path / "a.bin").read_bytes() == payload
    assert server.cfg["ranges"] == [None], "a stale part must be restarted, not resumed"


def test_a_part_larger_than_the_file_is_restarted(server, tmp_path):
    import json

    payload = b"m" * 1_000
    server.cfg["files"]["/a.bin"] = payload
    url = server.base + "/a.bin"
    (tmp_path / "a.bin.part").write_bytes(b"z" * 5_000)
    (tmp_path / "a.bin.part.meta.json").write_text(
        json.dumps({"url": url, "etag": '"v1"'}), "utf-8"
    )
    f = File(url, "a.bin", len(payload))
    res = downloader.fetch_file(Client(rate=1000, retries=0), f, opts(tmp_path, retries=0), set())
    assert res.state == "downloaded"
    assert (tmp_path / "a.bin").read_bytes() == payload


def test_a_real_sigint_stops_the_transfer_and_keeps_the_part(server, tmp_path):
    """The honest interrupt test: a delivered SIGINT, a blocked read, no stubs.

    ``KeyboardInterrupt`` only reaches the main thread, so the timer raises it
    for real while the read loop is stalled mid-body.
    """
    payload = b"z" * 400_000
    server.cfg["files"]["/a.bin"] = payload
    server.cfg["stall_after"] = 100_000
    server.cfg["stall_for"] = 5
    f = File(server.base + "/a.bin", "a.bin", len(payload))
    rec = Record(source="direct", target="t", files=[f])
    o = downloader.Options(out=tmp_path, concurrency=1, retries=3)

    timer = threading.Timer(1.5, signal.raise_signal, (signal.SIGINT,))
    timer.start()
    try:
        results = downloader.fetch_record(rec, o, Client(rate=100_000, retries=1))
    finally:
        timer.cancel()

    assert o.interrupted is True
    assert results[0].state == "failed"
    assert "interrupted" in results[0].error
    part = tmp_path / "a.bin.part"
    assert results[0].part == str(part)
    assert part.read_bytes()[:1] == b"z"
    assert part.stat().st_size > 0, "the bytes already read must survive"
    assert not (tmp_path / "a.bin").exists()

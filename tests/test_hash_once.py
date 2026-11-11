from __future__ import annotations

import hashlib
import json
import threading
import time

from acadfetch import downloader, verify
from acadfetch.client import Client
from acadfetch.sources.base import File, Record


def _opts(tmp_path, **kw):
    kw.setdefault("concurrency", 1)
    return downloader.Options(out=tmp_path, **kw)


def test_hashes_computes_every_algo_in_one_pass(tmp_path):
    path = tmp_path / "f.bin"
    payload = b"one pass"
    path.write_bytes(payload)

    got = verify.hashes(path, {"sha256", "md5"})

    assert got["sha256"] == hashlib.sha256(payload).hexdigest()
    assert got["md5"] == hashlib.md5(payload).hexdigest()


def test_a_fetch_never_rehashes_the_finished_file(server, tmp_path, monkeypatch):
    payload = b"payload" * 500
    server.cfg["files"]["/a.bin"] = payload
    f = File(
        server.base + "/a.bin",
        "a.bin",
        len(payload),
        "sha256:" + hashlib.sha256(payload).hexdigest(),
    )

    def boom(*_a, **_k):  # pragma: no cover - proves it is not reached
        raise AssertionError("hash_file was called")

    monkeypatch.setattr(verify, "hash_file", boom)
    res = downloader.fetch_file(Client(rate=1000, retries=0), f, _opts(tmp_path), set())

    assert res.state == "downloaded"
    assert res.verified is True
    assert res.sha256 == hashlib.sha256(payload).hexdigest()


def test_a_resumed_transfer_hashes_the_bytes_already_on_disk(server, tmp_path):
    payload = bytes(range(256)) * 40
    server.cfg["files"]["/a.bin"] = payload
    url = server.base + "/a.bin"
    (tmp_path / "a.bin.part").write_bytes(payload[:2000])
    (tmp_path / "a.bin.part.meta.json").write_text(
        json.dumps({"url": url, "etag": '"v1"', "size": len(payload)}), "utf-8"
    )
    f = File(url, "a.bin", len(payload), "sha256:" + hashlib.sha256(payload).hexdigest())

    res = downloader.fetch_file(Client(rate=1000, retries=0), f, _opts(tmp_path), set())

    assert res.state == "downloaded", res.error
    assert res.resumed_from == 2000
    assert res.verified is True
    assert res.sha256 == hashlib.sha256(payload).hexdigest()
    assert (tmp_path / "a.bin").read_bytes() == payload


def test_a_reused_file_is_read_once(server, tmp_path, monkeypatch):
    payload = b"reuse me"
    server.cfg["files"]["/a.bin"] = payload
    url = server.base + "/a.bin"
    (tmp_path / "a.bin").write_bytes(payload)
    (tmp_path / "manifest.json").write_text(
        json.dumps({"schema": 3, "files": [{"name": "a.bin", "url": url}]}), "utf-8"
    )
    calls = []
    real = verify.hashes

    def counting(path, algos):
        calls.append(algos)
        return real(path, algos)

    monkeypatch.setattr(verify, "hashes", counting)
    monkeypatch.setattr(
        verify, "hash_file", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("rehash"))
    )

    opts = _opts(tmp_path)
    opts.trusted = {"a.bin": url}  # what fetch_record loads from the manifest
    res = downloader.fetch_file(
        Client(rate=1000, retries=0), File(url, "a.bin", len(payload)), opts, set()
    )

    assert res.state == "skipped"
    assert len(calls) == 1, "one read produces the verdict and the manifest hash"
    assert res.sha256 == hashlib.sha256(payload).hexdigest()


def test_the_in_flight_window_is_bounded(server, tmp_path, monkeypatch):
    files = []
    for i in range(30):
        server.cfg["files"][f"/{i}.bin"] = b"x"
        files.append(File(server.base + f"/{i}.bin", f"{i}.bin", 1))
    record = Record(source="direct", target="t", files=files)

    state = {"now": 0, "max": 0}
    lock = threading.Lock()
    real = downloader.fetch_file

    def watched(client, file, opts, taken):
        with lock:
            state["now"] += 1
            state["max"] = max(state["max"], state["now"])
        try:
            time.sleep(0.01)
            return real(client, file, opts, taken)
        finally:
            with lock:
                state["now"] -= 1

    monkeypatch.setattr(downloader, "fetch_file", watched)
    results = downloader.fetch_record(
        record, downloader.Options(out=tmp_path, concurrency=3), Client(rate=10000)
    )

    assert len(results) == 30
    assert state["max"] <= 6, state["max"]
    assert state["max"] >= 2

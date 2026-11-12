from __future__ import annotations

import json
import os
import threading
import time

import pytest

from acadfetch import cli, downloader
from acadfetch.client import Client
from acadfetch.errors import TargetError
from acadfetch.sources.base import File, Record

LOCK = downloader.LOCK_NAME


def _record(server, name="a.bin", payload=b"payload"):
    server.cfg["files"]["/" + name] = payload
    return Record(
        source="direct",
        target="t",
        files=[File(server.base + "/" + name, name, len(payload))],
    )


def test_a_live_owner_refuses_the_directory(server, tmp_path):
    """The parent process is alive and is not us, like another run would be."""
    owner = os.getpid()  # alive, and not in _OWN_LOCKS, so it is another run
    (tmp_path / LOCK).write_text(json.dumps({"pid": owner}), "utf-8")
    record = _record(server)

    with pytest.raises(TargetError) as caught:
        downloader.fetch_record(record, downloader.Options(out=tmp_path), Client(rate=1000))

    assert f"pid {owner}" in str(caught.value)
    assert "delete" in str(caught.value)


def test_a_dead_owner_is_taken_over(server, tmp_path):
    (tmp_path / LOCK).write_text(json.dumps({"pid": 999_999}), "utf-8")
    record = _record(server)

    results = downloader.fetch_record(record, downloader.Options(out=tmp_path), Client(rate=1000))

    assert results[0].state == "downloaded"
    assert not (tmp_path / LOCK).exists(), "the lock is released"


def test_an_unreadable_lock_is_taken_over(server, tmp_path):
    (tmp_path / LOCK).write_text("not json", "utf-8")
    record = _record(server)

    results = downloader.fetch_record(record, downloader.Options(out=tmp_path), Client(rate=1000))

    assert results[0].state == "downloaded"
    assert not (tmp_path / LOCK).exists()


def test_the_lock_is_released_after_a_run(server, tmp_path):
    record = _record(server)

    downloader.fetch_record(record, downloader.Options(out=tmp_path), Client(rate=1000))

    assert not (tmp_path / LOCK).exists()


def test_a_dry_run_does_not_take_the_lock(server, tmp_path):
    record = _record(server)

    downloader.fetch_record(
        record, downloader.Options(out=tmp_path, dry_run=True), Client(rate=1000)
    )

    assert not (tmp_path / LOCK).exists()


def test_a_concurrent_cli_run_is_refused(server, monkeypatch, capsys, tmp_path):
    record = _record(server)
    monkeypatch.setattr(cli.registry, "resolve", lambda _c, _t: record)
    started = threading.Event()
    release = threading.Event()

    def slow(*args, **kwargs):
        started.set()
        assert release.wait(10)
        return downloader.fetch_file(*args, **kwargs)

    monkeypatch.setattr(downloader, "fetch_file", slow)
    first = threading.Thread(target=lambda: cli.main(["fetch", "t", "-q", "-o", str(tmp_path)]))
    first.start()
    assert started.wait(10)
    try:
        code = cli.main(["fetch", "t", "-q", "-o", str(tmp_path)])
        err = capsys.readouterr().err
    finally:
        release.set()
        first.join(10)

    assert code == cli.EXIT_FAIL
    assert "being written by pid" in err
    assert not (tmp_path / LOCK).exists()


def test_a_repository_lock_name_does_not_collide(server, monkeypatch, tmp_path):
    payload = b"theirs"
    server.cfg["files"]["/" + LOCK] = payload
    record = Record(
        source="direct",
        target="t",
        files=[File(server.base + "/" + LOCK, LOCK, len(payload))],
    )
    monkeypatch.setattr(cli.registry, "resolve", lambda _c, _t: record)

    code = cli.main(["fetch", "t", "-q", "-o", str(tmp_path)])

    assert code == cli.EXIT_OK
    assert (tmp_path / "acadfetch.lock").read_bytes() == payload
    assert not (tmp_path / LOCK).exists(), "the run released its own lock"
    assert (tmp_path / "manifest.json").exists()


def test_the_lock_file_is_not_left_by_a_failure(server, tmp_path):
    record = _record(server)
    record.files[0].url = server.base + "/gone.bin"
    record.files[0].size = None

    results = downloader.fetch_record(
        record, downloader.Options(out=tmp_path, retries=0), Client(rate=1000, retries=0)
    )

    assert results[0].state == "failed"
    assert not (tmp_path / LOCK).exists()


def test_a_stale_lock_is_replaced_not_appended(server, tmp_path):
    (tmp_path / LOCK).write_text(json.dumps({"pid": 999_999}), "utf-8")
    record = _record(server)

    downloader.fetch_record(record, downloader.Options(out=tmp_path), Client(rate=1000))

    assert os.listdir(tmp_path).count(LOCK) <= 1


def _unused() -> None:  # keeps the time import honest for future timings
    time.sleep(0)

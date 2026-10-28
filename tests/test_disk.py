from __future__ import annotations

import os
import shutil

import pytest

from acadfetch import downloader
from acadfetch.client import Client
from acadfetch.errors import HttpError
from acadfetch.sources.base import File, Record


@pytest.fixture
def record(server):
    server.cfg["files"]["/big.bin"] = b"q" * 2_000
    return Record(
        source="direct",
        target="t",
        files=[File(server.base + "/big.bin", "big.bin", 2_000)],
    )


def _usage(free: int):
    return shutil._ntuple_diskusage(0, 0, free)  # noqa: SLF001


def test_a_run_that_cannot_fit_is_refused_before_any_request(record, server, tmp_path, monkeypatch):
    monkeypatch.setattr(downloader.shutil, "disk_usage", lambda _p: _usage(1_000))

    with pytest.raises(HttpError) as caught:
        downloader.fetch_record(record, downloader.Options(out=tmp_path), Client(retries=0))

    assert caught.value.reason == "disk"
    assert "free" in str(caught.value)
    assert server.cfg["hits"] == {}, "refused before spending a request"
    assert not (tmp_path / "big.bin.part").exists()


def test_the_margin_is_kept_spare(record, server, tmp_path, monkeypatch):
    # Exactly enough, with no room for the manifest: still refused.
    monkeypatch.setattr(downloader.shutil, "disk_usage", lambda _p: _usage(2_000))
    with pytest.raises(HttpError):
        downloader.fetch_record(record, downloader.Options(out=tmp_path), Client(retries=0))

    monkeypatch.setattr(
        downloader.shutil, "disk_usage", lambda _p: _usage(2_000 + downloader.DISK_MARGIN)
    )
    results = downloader.fetch_record(record, downloader.Options(out=tmp_path), Client(retries=0))
    assert results[0].state == "downloaded"


def test_a_part_already_on_disk_is_not_counted_again(server, tmp_path, monkeypatch):
    """Only the remainder has to fit, or a resumed 40 GB file never passes."""
    server.cfg["files"]["/big.bin"] = b"q" * 10_000
    f = File(server.base + "/big.bin", "big.bin", 10_000)
    (tmp_path / "big.bin.part").write_bytes(b"q" * 9_000)
    free = 1_000 + downloader.DISK_MARGIN
    monkeypatch.setattr(downloader.shutil, "disk_usage", lambda _p: _usage(free))

    assert downloader.check_disk([f], tmp_path) == (free, 1_000)


def test_an_unwritable_sized_file_is_skipped_from_the_sum(tmp_path):
    f = File("http://x/a.bin", "a.bin", 5_000)
    (tmp_path / "a.bin").write_bytes(b"x")

    assert downloader.check_disk([f], tmp_path) == (0, 0)


def test_the_bytes_are_flushed_before_the_rename(record, tmp_path, monkeypatch):
    calls = []
    real = os.fsync

    def spy(fd):
        calls.append("fsync")
        return real(fd)

    monkeypatch.setattr(downloader.os, "fsync", spy)
    downloader.fetch_record(record, downloader.Options(out=tmp_path), Client(retries=0))

    assert calls == ["fsync"]


def test_no_fsync_skips_it(record, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(downloader.os, "fsync", lambda fd: calls.append(fd))

    downloader.fetch_record(
        record, downloader.Options(out=tmp_path, fsync=False), Client(retries=0)
    )

    assert calls == []

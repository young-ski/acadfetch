from __future__ import annotations

import os
from pathlib import Path

import pytest

from acadfetch import atomic


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(atomic.time, "sleep", lambda _s: None)


def failing_once(monkeypatch, calls):
    """Make os.replace fail with a sharing violation, then behave."""
    real = os.replace

    def flaky(src, dst):
        calls.append((src, dst))
        if len(calls) < 3:
            raise PermissionError(13, "in use", str(dst))
        return real(src, dst)

    monkeypatch.setattr(atomic.os, "replace", flaky)


def test_replace_retries_a_sharing_violation(tmp_path, monkeypatch, no_sleep):
    """Windows refuses os.replace while anyone has the target open."""
    calls: list[tuple] = []
    failing_once(monkeypatch, calls)
    src, dst = tmp_path / "a.tmp", tmp_path / "a.bin"
    src.write_bytes(b"new")
    dst.write_bytes(b"old")
    atomic.replace(src, dst)
    assert dst.read_bytes() == b"new"
    assert len(calls) == 3


def test_replace_gives_up_after_the_budget(tmp_path, monkeypatch, no_sleep):
    calls: list[tuple] = []

    def always(src, dst):
        calls.append((src, dst))
        raise PermissionError(13, "still in use", str(dst))

    monkeypatch.setattr(atomic.os, "replace", always)
    src, dst = tmp_path / "a.tmp", tmp_path / "a.bin"
    src.write_bytes(b"new")
    with pytest.raises(PermissionError):
        atomic.replace(src, dst)
    assert len(calls) == atomic.ATTEMPTS


def test_replace_does_not_retry_an_unrelated_error(tmp_path, monkeypatch, no_sleep):
    calls: list[tuple] = []

    def broken(src, dst):
        calls.append((src, dst))
        raise OSError(2, "no such file")

    monkeypatch.setattr(atomic.os, "replace", broken)
    with pytest.raises(OSError) as info:
        atomic.replace(tmp_path / "a.tmp", tmp_path / "a.bin")
    assert info.value.errno == 2
    assert len(calls) == 1


def test_read_text_retries_a_sharing_violation(tmp_path, monkeypatch, no_sleep):
    path = tmp_path / "m.json"
    path.write_text("payload", "utf-8")
    real = Path.read_text
    calls: list[int] = []

    def flaky(self, *a, **kw):
        calls.append(1)
        if len(calls) < 3:
            raise PermissionError(13, "in use", str(self))
        return real(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", flaky)
    assert atomic.read_text(path) == "payload"
    assert len(calls) == 3


def test_unlink_swallows_a_lost_race(tmp_path, monkeypatch, no_sleep):
    path = tmp_path / "gone.part"
    path.write_bytes(b"x")
    path.unlink()
    atomic.unlink(path)  # second removal must not raise

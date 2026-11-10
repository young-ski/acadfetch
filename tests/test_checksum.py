from __future__ import annotations

import hashlib
import json

import pytest

from acadfetch import cli
from acadfetch.sources.base import File, Record

PAYLOAD = b"checksum payload" * 64


def _run(capsys, *argv):
    code = cli.main([str(a) for a in argv])
    out, err = capsys.readouterr()
    return code, out, err


@pytest.fixture
def served(server, monkeypatch):
    server.cfg["files"]["/a.bin"] = PAYLOAD

    def resolve(_client, _target):
        return Record(
            source="direct",
            target=server.base + "/a.bin",
            # A wrong published checksum, so precedence is observable.
            files=[File(server.base + "/a.bin", "a.bin", len(PAYLOAD), "md5:" + "0" * 32)],
        )

    monkeypatch.setattr(cli.registry, "resolve", resolve)
    return server


def test_a_supplied_checksum_is_what_verification_uses(served, capsys, tmp_path):
    digest = hashlib.md5(PAYLOAD).hexdigest()

    code, out, _ = _run(
        capsys, "fetch", "x", "--json", "--checksum", f"md5:{digest}", "-o", tmp_path
    )

    assert code == cli.EXIT_OK
    row = json.loads(out)["files"][0]
    assert row["verified"] is True
    assert row["verified_reason"] == "matched-md5"
    manifest = json.loads((tmp_path / "manifest.json").read_text("utf-8"))
    assert manifest["files"][0]["checksum"] == f"md5:{digest}"
    assert manifest["files"][0]["checksum_source"] == "cli"


def test_a_wrong_checksum_fails_and_leaves_no_file(served, capsys, tmp_path):
    code, out, _ = _run(
        capsys, "fetch", "x", "--json", "--checksum", "sha256:" + "0" * 64, "-o", tmp_path
    )

    assert code == cli.EXIT_FAIL
    assert json.loads(out)["files"][0]["verified"] is False
    assert not (tmp_path / "a.bin").exists()


def test_a_published_checksum_loses_to_the_explicit_one(served, capsys, tmp_path):
    """The source's md5 is wrong here; --checksum is the one that must decide."""
    digest = hashlib.sha256(PAYLOAD).hexdigest()

    code, _, _ = _run(capsys, "fetch", "x", "--checksum", f"sha256:{digest}", "-q", "-o", tmp_path)

    assert code == cli.EXIT_OK


@pytest.mark.parametrize("bad", ["md5:zz", "sha1:" + "0" * 40, "crc32:12", "", "md5"])
def test_a_bad_format_is_refused_before_the_request(bad, monkeypatch, capsys):
    def boom(*_a, **_k):  # pragma: no cover - proves it is not reached
        raise AssertionError("resolved")

    monkeypatch.setattr(cli.registry, "resolve", boom)
    with pytest.raises(SystemExit) as caught:
        cli.main(["fetch", "x", "--checksum", bad])
    assert caught.value.code == 2


def test_the_supplied_checksum_reaches_a_plan(served, capsys, tmp_path):
    digest = hashlib.md5(PAYLOAD).hexdigest()

    code, out, _ = _run(capsys, "fetch", "x", "-n", "--json", "--checksum", f"md5:{digest}")

    assert code == cli.EXIT_OK
    assert json.loads(out)["files"][0]["verified_reason"] == "unchecked"

from __future__ import annotations

import hashlib

import pytest

from acadfetch import verify

DATA = b"acadfetch test payload\n"
MD5 = hashlib.md5(DATA).hexdigest()
SHA = hashlib.sha256(DATA).hexdigest()


@pytest.mark.parametrize(
    "raw,algo",
    [
        (f"md5:{MD5}", "md5"),
        (f"MD5:{MD5.upper()}", "md5"),
        (f"sha256:{SHA}", "sha256"),
        (SHA, "sha256"),
        (MD5, "md5"),
    ],
)
def test_parse_accepts_known_forms(raw, algo):
    parsed = verify.parse(raw)
    assert parsed is not None
    assert parsed[0] == algo


@pytest.mark.parametrize(
    "raw",
    [None, "", "crc32:1234", "sha256:tooshort", "sha256:zz" + "0" * 62, "md5:" + "g" * 32],
)
def test_parse_rejects_junk(raw):
    assert verify.parse(raw) is None


def test_matches_true(tmp_path):
    path = tmp_path / "f.bin"
    path.write_bytes(DATA)
    assert verify.matches(path, f"sha256:{SHA}") is True


def test_matches_false(tmp_path):
    path = tmp_path / "f.bin"
    path.write_bytes(DATA)
    assert verify.matches(path, f"sha256:{'0' * 64}") is False


def test_matches_none_when_unverifiable(tmp_path):
    path = tmp_path / "f.bin"
    path.write_bytes(DATA)
    assert verify.matches(path, None) is None


def test_describe_truncates():
    assert verify.describe(f"sha256:{SHA}").startswith("sha256:")
    assert verify.describe(None) == "no published checksum"

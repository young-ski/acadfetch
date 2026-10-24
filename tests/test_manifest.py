from __future__ import annotations

import json

import pytest

from acadfetch import manifest
from acadfetch.sources.base import File, Record, Result


def record(tmp_path):
    (tmp_path / "a.csv").write_bytes(b"a")
    return Record(
        source="zenodo",
        target="zenodo:1",
        files=[File("https://example/a.csv", "a.csv", 1, "md5:" + "0" * 32)],
        title="t",
        doi="10.5281/zenodo.1",
        license="cc-by-4.0",
    )


def test_build_records_checksum_and_sha256(tmp_path):
    rec = record(tmp_path)
    results = [
        Result(rec.files[0], "downloaded", path=str(tmp_path / "a.csv"), bytes=1, verified=False)
    ]
    data = manifest.build(rec, results)
    assert data["license"] == "cc-by-4.0"
    assert data["schema"] == manifest.SCHEMA
    entry = data["files"][0]
    assert entry["checksum"].startswith("md5:")
    assert entry["sha256"]
    assert entry["state"] == "downloaded"


def test_build_without_results_has_no_sha256():
    rec = Record(source="zenodo", target="zenodo:1", files=[File("https://example/a", "a")])
    entry = manifest.build(rec, [])["files"][0]
    assert entry["sha256"] is None
    assert entry["state"] == "missing"


def test_write_load_roundtrip(tmp_path):
    rec = record(tmp_path)
    path = tmp_path / "m.json"
    manifest.write(path, manifest.build(rec, []))
    assert manifest.load(path)["doi"] == "10.5281/zenodo.1"
    assert json.loads(path.read_text())["schema"] == manifest.SCHEMA


def test_write_keeps_utf8_readable(tmp_path):
    rec = Record(source="zenodo", target="x", title="中文标题", files=[])
    path = tmp_path / "m.json"
    manifest.write(path, manifest.build(rec, []))
    assert "中文标题" in path.read_text("utf-8")


def test_load_rejects_future_schema(tmp_path):
    path = tmp_path / "m.json"
    path.write_text(json.dumps({"schema": 99}))
    with pytest.raises(ValueError, match="unsupported"):
        manifest.load(path)


def test_verify_manifest_flags_tampering(tmp_path):
    rec = record(tmp_path)
    results = [Result(rec.files[0], "downloaded", path=str(tmp_path / "a.csv"), bytes=1)]
    mp = tmp_path / "m.json"
    manifest.write(mp, manifest.build(rec, results))

    rows = manifest.verify_manifest(mp, tmp_path)
    assert rows == [{"name": "a.csv", "status": "ok", "sha256": rows[0]["sha256"]}]

    (tmp_path / "a.csv").write_bytes(b"tampered")
    rows = manifest.verify_manifest(mp, tmp_path)
    assert rows[0]["status"] == "MISMATCH"


def test_verify_manifest_reports_missing(tmp_path):
    rec = record(tmp_path)
    mp = tmp_path / "m.json"
    manifest.write(mp, manifest.build(rec, []))
    (tmp_path / "a.csv").unlink()
    assert manifest.verify_manifest(mp, tmp_path)[0]["status"] == "missing"

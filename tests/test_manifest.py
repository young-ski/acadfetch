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


def test_two_files_with_one_name_keep_distinct_rows(tmp_path):
    """Schema 2 records the name on disk, so a deduplicated pair still verifies."""
    (tmp_path / "a.csv").write_bytes(b"one")
    (tmp_path / "a (2).csv").write_bytes(b"two")
    rec = Record(
        source="direct",
        target="t",
        files=[File("https://example/a.csv", "a.csv"), File("https://example/dup/a.csv", "a.csv")],
    )
    results = [
        Result(rec.files[0], "downloaded", path=str(tmp_path / "a.csv"), bytes=3, name="a.csv"),
        Result(
            rec.files[1], "downloaded", path=str(tmp_path / "a (2).csv"), bytes=3, name="a (2).csv"
        ),
    ]
    mp = tmp_path / "m.json"
    manifest.write(mp, manifest.build(rec, results))
    assert [e["name"] for e in manifest.load(mp)["files"]] == ["a.csv", "a (2).csv"]
    assert [r["status"] for r in manifest.verify_manifest(mp, tmp_path)] == ["ok", "ok"]


def test_duplicate_urls_pair_by_file_not_by_url(tmp_path):
    """Two entries sharing one URL must not collapse into the same result."""
    import hashlib

    (tmp_path / "a.bin").write_bytes(b"a")
    (tmp_path / "b.bin").write_bytes(b"b")
    rec = Record(
        source="direct",
        target="t",
        files=[File("https://example/same", "a.bin"), File("https://example/same", "b.bin")],
    )
    results = [
        Result(rec.files[0], "downloaded", path=str(tmp_path / "a.bin"), bytes=1, name="a.bin"),
        Result(rec.files[1], "downloaded", path=str(tmp_path / "b.bin"), bytes=1, name="b.bin"),
    ]
    digests = [e["sha256"] for e in manifest.build(rec, results)["files"]]
    assert digests == [hashlib.sha256(b"a").hexdigest(), hashlib.sha256(b"b").hexdigest()]


def test_verified_reason_separates_the_two_kinds_of_null():
    rec = Record(source="zenodo", target="t", files=[File("https://example/a", "a")])
    unchecked = manifest.build(rec, [])["files"][0]
    assert unchecked["verified"] is None
    assert unchecked["verified_reason"] == "no-checksum-published"

    rec.files[0].checksum = "md5:not-hex"
    assert manifest.build(rec, [])["files"][0]["verified_reason"] == "unparseable-checksum"


def test_schema_1_is_still_read_and_can_be_flagged(tmp_path):
    import hashlib

    (tmp_path / "a.csv").write_bytes(b"a")
    path = tmp_path / "m.json"
    path.write_text(
        json.dumps(
            {"schema": 1, "files": [{"name": "a.csv", "sha256": hashlib.sha256(b"a").hexdigest()}]}
        ),
        "utf-8",
    )
    assert manifest.peek_schema(path) == 1
    assert manifest.load(path)["schema"] == 1
    assert manifest.verify_manifest(path, tmp_path)[0]["status"] == "ok"
    assert manifest.peek_schema(tmp_path / "absent.json") is None


def test_a_failed_entry_says_transfer_failed(tmp_path):
    rec = record(tmp_path)
    failed = Result(rec.files[0], "failed", error="HTTP 500", name="a.csv")
    assert manifest.build(rec, [failed])["files"][0]["verified_reason"] == "transfer-failed"

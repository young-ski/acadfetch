from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from acadfetch import cli, manifest
from acadfetch.sources.base import File, Record

PAYLOAD = b"json body" * 500


def _run(capsys, *argv):
    code = cli.main([str(a) for a in argv])
    out, err = capsys.readouterr()
    return code, out, err


@pytest.fixture
def served(server, monkeypatch):
    """Two resolvable files, so `fetch` never touches the network."""
    server.cfg["files"]["/a.bin"] = PAYLOAD
    server.cfg["files"]["/b.bin"] = b"second"

    def resolve(client, target):
        assert target == "zenodo:1"
        return Record(
            source="zenodo",
            target="zenodo:1",
            doi="10.5281/zenodo.1",
            license="cc-by-4.0",
            landing="https://zenodo.org/records/1",
            files=[
                File(server.base + "/a.bin", "a.bin", len(PAYLOAD)),
                File(server.base + "/b.bin", "b.bin", 6),
            ],
        )

    monkeypatch.setattr(cli.registry, "resolve", resolve)
    return server


def test_json_is_one_document_and_progress_goes_to_stderr(served, capsys, tmp_path):
    code, out, err = _run(capsys, "fetch", "zenodo:1", "--json", "-o", tmp_path)

    doc = json.loads(out)  # all of stdout parses; no progress line spoils it
    assert code == cli.EXIT_OK
    assert doc["report"] == cli.REPORT_SCHEMA
    assert doc["command"] == "fetch"
    assert doc["target"] == "zenodo:1"
    assert doc["source"] == "zenodo"
    assert doc["dry_run"] is False
    assert doc["error"] is None
    assert [f["name"] for f in doc["files"]] == ["a.bin", "b.bin"]
    assert [f["state"] for f in doc["files"]] == ["downloaded", "downloaded"]
    assert [f["bytes"] for f in doc["files"]] == [len(PAYLOAD), 6]
    assert doc["files"][0]["verified"] is None
    assert doc["files"][0]["verified_reason"] == "no-checksum-published"
    assert doc["files"][0]["part"] is None
    assert doc["summary"] == {"total": 2, "downloaded": 2, "skipped": 0, "failed": 0, "ok": True}
    assert doc["elapsed_s"] >= 0
    assert "2 file(s)" in err, "progress belongs on stderr, not in the document"


def test_json_does_not_change_the_exit_code(served, capsys, tmp_path):
    plain = _run(capsys, "fetch", "zenodo:1", "-q", "-o", tmp_path / "p")[0]
    as_json = _run(capsys, "fetch", "zenodo:1", "--json", "-o", tmp_path / "j")[0]
    assert plain == as_json == cli.EXIT_OK


def test_json_dry_run_plans_without_bytes(served, capsys, tmp_path):
    code, out, _ = _run(capsys, "fetch", "zenodo:1", "-n", "--json", "-o", tmp_path)

    doc = json.loads(out)
    assert code == cli.EXIT_OK
    assert doc["dry_run"] is True
    assert [f["state"] for f in doc["files"]] == ["planned", "planned"]
    assert [f["bytes"] for f in doc["files"]] == [0, 0]
    assert doc["summary"]["total"] == 2
    assert list(tmp_path.iterdir()) == []


def test_json_honours_only_in_a_dry_run(served, capsys, tmp_path):
    code, out, _ = _run(capsys, "fetch", "zenodo:1", "-n", "--json", "--only", "b.bin")

    doc = json.loads(out)
    assert code == cli.EXIT_OK
    assert [f["name"] for f in doc["files"]] == ["b.bin"]


def test_json_reports_a_checksum_mismatch(server, monkeypatch, capsys, tmp_path):
    server.cfg["files"]["/a.bin"] = PAYLOAD

    def resolve(client, target):
        return Record(
            source="zenodo",
            target="zenodo:1",
            files=[File(server.base + "/a.bin", "a.bin", len(PAYLOAD), "md5:" + "0" * 32)],
        )

    monkeypatch.setattr(cli.registry, "resolve", resolve)

    code, out, _ = _run(capsys, "fetch", "zenodo:1", "--json", "-o", tmp_path)

    row = json.loads(out)["files"][0]
    assert code == cli.EXIT_FAIL
    assert row["state"] == "failed"
    assert row["verified"] is False
    assert row["verified_reason"] == "mismatch"


def test_a_bad_target_still_yields_a_document(capsys):
    code, out, err = _run(capsys, "fetch", "nonsense://x", "--json")

    doc = json.loads(out)
    assert code == cli.EXIT_FAIL
    assert "cannot tell what" in doc["error"]
    assert "acadfetch:" in err


def test_json_failure_keeps_the_part_and_names_it(served, capsys, tmp_path):
    served.cfg["truncate_at"] = 4000  # every body stops short
    served.cfg["truncate_times"] = 9
    code, out, _ = _run(
        capsys, "fetch", "zenodo:1", "--only", "a.bin", "--retries", "0", "--json", "-o", tmp_path
    )

    doc = json.loads(out)
    row = doc["files"][0]
    assert code == cli.EXIT_FAIL
    assert row["state"] == "failed"
    assert row["part"] == "a.bin.part"
    assert row["bytes"] == 4000
    assert (tmp_path / "a.bin.part").exists()
    assert doc["summary"] == {"total": 1, "downloaded": 0, "skipped": 0, "failed": 1, "ok": False}


def test_verify_json_flags_a_tampered_file(capsys, tmp_path):
    data = manifest.build(
        Record(source="zenodo", target="zenodo:1", files=[File("http://x/a.bin", "a.bin", 8)]),
        [],
        None,
    )
    data["files"][0]["md5"] = hashlib.md5(b"original").hexdigest()
    manifest.write(tmp_path / "manifest.json", data)
    (tmp_path / "a.bin").write_bytes(b"tampered")

    code, out, _ = _run(capsys, "verify", tmp_path / "manifest.json", "--json")

    doc = json.loads(out)
    assert code == cli.EXIT_FAIL
    assert doc["summary"] == {
        "total": 1,
        "ok": 0,
        "bad": 1,
        "skipped": 0,
        "ok_run": False,
    }
    assert doc["rows"][0]["status"] == "MISMATCH"
    assert doc["root"] == str(tmp_path)


def test_dry_run_reports_disk_headroom(served, capsys, tmp_path):
    code, out, _ = _run(capsys, "fetch", "zenodo:1", "-n", "--json", "-o", tmp_path)

    disk = json.loads(out)["disk"]
    assert code == cli.EXIT_OK
    assert disk["needed"] == len(PAYLOAD) + 6
    assert disk["free"] > disk["needed"]
    assert disk["ok"] is True


# -- schema 3: a --only manifest still verifies ---------------------------


def test_a_partial_fetch_verifies_clean(served, capsys, tmp_path):
    """The common path: fetch one file, then verify what you asked for."""
    out_dir = tmp_path / "o"
    code, _, _ = _run(capsys, "fetch", "zenodo:1", "--only", "a.bin", "-q", "-o", out_dir)
    assert code == cli.EXIT_OK

    data = json.loads((out_dir / "manifest.json").read_text("utf-8"))
    assert data["schema"] == 3
    assert [(f["state"], f["selected"]) for f in data["files"]] == [
        ("downloaded", True),
        ("not-selected", False),
    ]

    code, out, err = _run(capsys, "verify", out_dir / "manifest.json")
    assert code == cli.EXIT_OK, err
    assert "1/2 ok, 1 skipped" in out

    code, _, _ = _run(capsys, "verify", out_dir / "manifest.json", "--require-all")
    assert code == cli.EXIT_FAIL


def test_verify_json_counts_skipped(served, capsys, tmp_path):
    out_dir = tmp_path / "o"
    _run(capsys, "fetch", "zenodo:1", "--only", "a.bin", "-q", "-o", out_dir)

    code, out, _ = _run(capsys, "verify", out_dir / "manifest.json", "--json")

    doc = json.loads(out)
    assert code == cli.EXIT_OK
    assert doc["summary"] == {"total": 2, "ok": 1, "bad": 0, "skipped": 1, "ok_run": True}
    assert [row["status"] for row in doc["rows"]] == ["ok", "skipped"]
    assert doc["rows"][1]["state"] == "not-selected"
    assert doc["rows"][0]["sha256"]


def test_require_all_in_json_is_a_failure(served, capsys, tmp_path):
    out_dir = tmp_path / "o"
    _run(capsys, "fetch", "zenodo:1", "--only", "a.bin", "-q", "-o", out_dir)

    code, out, _ = _run(capsys, "verify", out_dir / "manifest.json", "--json", "--require-all")

    doc = json.loads(out)
    assert code == cli.EXIT_FAIL
    assert doc["summary"]["bad"] == 1
    assert doc["summary"]["ok_run"] is False


# -- schema 3: credential query parameters --------------------------------


def _signed_record(server):
    signed = server.base + "/a.bin?X-Amz-Signature=SECRET&Expires=1"

    def resolve(_client, _target):
        return Record(
            source="direct",
            target=signed,
            landing=signed,
            files=[File(signed, "a.bin", 10)],
        )

    server.cfg["files"]["/a.bin"] = b"x" * 10
    return resolve


def test_json_redacts_a_presigned_url(server, monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli.registry, "resolve", _signed_record(server))

    code, out, _ = _run(capsys, "fetch", "signed", "--json", "-o", tmp_path)

    doc = json.loads(out)
    assert code == cli.EXIT_OK
    assert "SECRET" not in out
    assert doc["url_redacted"] is True
    assert doc["files"][0]["http_status"] == 200
    assert doc["files"][0]["final_url"].startswith(server.base)


def test_record_full_url_keeps_the_signature(server, monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli.registry, "resolve", _signed_record(server))

    code, out, _ = _run(capsys, "fetch", "signed", "--json", "--record-full-url", "-o", tmp_path)

    assert code == cli.EXIT_OK
    assert "SECRET" in out
    assert json.loads(out)["url_redacted"] is False


def test_the_default_directory_drops_the_query(server, monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli.registry, "resolve", _signed_record(server))
    monkeypatch.chdir(tmp_path)

    code, out, _ = _run(capsys, "fetch", "signed", "--json", "-n")

    assert code == cli.EXIT_OK
    slug = Path(json.loads(out)["out"]).name
    assert slug.startswith("direct-127.0.0.1-") and slug.endswith("-a.bin")
    assert "SECRET" not in slug


def test_every_report_field_is_in_the_contract(served, capsys, tmp_path):
    """A field added to the document without a line in the contract fails here."""
    docs = Path("docs/json-report.md").read_text("utf-8")

    _, out, _ = _run(capsys, "fetch", "zenodo:1", "--json", "-o", tmp_path)
    fetch_doc = json.loads(out)
    for key in fetch_doc:
        assert f"`{key}`" in docs, f"fetch field {key!r} is undocumented"
    for key in fetch_doc["files"][0]:
        assert f"`{key}`" in docs, f"file field {key!r} is undocumented"

    manifest_path = tmp_path / "manifest.json"
    _, vout, _ = _run(capsys, "verify", manifest_path, "--json")
    verify_doc = json.loads(vout)
    for key in verify_doc:
        assert f"`{key}`" in docs, f"verify field {key!r} is undocumented"
    for value in ("escaped", "malformed", "remote_match", "remote_unavailable"):
        assert value in docs, f"{value!r} is undocumented"


def test_the_report_and_the_manifest_agree_on_a_cause(served, capsys, tmp_path):
    """One run, one vocabulary: a failed row must not carry two reasons.

    These are two documents describing the same file, and a script may hold both.
    ``verified_reason`` used to say ``transfer-failed`` in the manifest and
    ``no-checksum-published`` in the report, which reads as two different bugs.
    """
    served.cfg["forbidden"].add("/a.bin")

    code, out, _ = _run(capsys, "fetch", "zenodo:1", "--json", "-o", tmp_path)

    assert code == cli.EXIT_PARTIAL
    row = json.loads(out)["files"][0]
    assert row["state"] == "failed"
    written = json.loads((tmp_path / "manifest.json").read_text("utf-8"))
    assert written["files"][0]["verified_reason"] == "transfer-failed"
    assert row["verified_reason"] == written["files"][0]["verified_reason"]

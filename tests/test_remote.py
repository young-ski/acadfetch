from __future__ import annotations

import base64
import hashlib
import json

from acadfetch import cli
from acadfetch.sources.base import File, Record

BODY = b"remote check payload" * 40
MD5 = hashlib.md5(BODY)
GOOD = "md5:" + MD5.hexdigest()


def _manifest(tmp_path, url, checksum=GOOD, body=BODY, file_url=None):
    (tmp_path / "a.bin").write_bytes(body)
    data = {
        "schema": 3,
        "source": "direct",
        "target": url,
        "files": [
            {
                "name": "a.bin",
                "url": file_url or url,
                "checksum": checksum,
                "sha256": hashlib.sha256(body).hexdigest(),
            }
        ],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(data), "utf-8")
    return path


def _run(capsys, *argv):
    code = cli.main([str(a) for a in argv])
    out, err = capsys.readouterr()
    return code, out, err


def _served(server, headers=None, body=BODY):
    server.cfg["files"]["/a.bin"] = body
    if headers:
        server.cfg["headers"]["/a.bin"] = headers
    return server.base + "/a.bin"


def test_a_matching_published_checksum_is_a_match(server, capsys, tmp_path):
    url = _served(server, {"Content-MD5": base64.b64encode(MD5.digest()).decode()})

    code, out, _ = _run(capsys, "verify", _manifest(tmp_path, url), "--against-remote", "--json")

    doc = json.loads(out)
    assert code == cli.EXIT_OK
    assert doc["rows"][0]["remote"] == "remote_match"
    assert doc["remote"]["remote_match"] == 1


def test_a_changed_published_checksum_fails_even_when_local_is_ok(server, capsys, tmp_path):
    """The manifest says one thing, the source another: local hashes still agree."""
    wrong = "md5:" + "0" * 32
    url = _served(server, {"Content-MD5": base64.b64encode(MD5.digest()).decode()})

    code, out, err = _run(
        capsys, "verify", _manifest(tmp_path, url, wrong), "--against-remote", "--json"
    )

    doc = json.loads(out)
    assert doc["rows"][0]["status"] == "ok", "the bytes are fine locally"
    assert doc["rows"][0]["remote"] == "remote_mismatch"
    assert code == cli.EXIT_FAIL
    assert "no longer match" in err


def test_no_published_checksum_is_reported_not_passed(server, capsys, tmp_path):
    url = _served(server)

    code, out, _ = _run(capsys, "verify", _manifest(tmp_path, url), "--against-remote", "--json")

    doc = json.loads(out)
    assert code == cli.EXIT_OK
    assert doc["rows"][0]["remote"] == "remote_no_checksum"


def test_an_unreachable_target_is_unavailable_and_not_fatal(server, capsys, tmp_path):
    url = server.base + "/gone.bin"  # never registered

    code, out, err = _run(capsys, "verify", _manifest(tmp_path, url), "--against-remote", "--json")

    doc = json.loads(out)
    assert code == cli.EXIT_OK
    assert doc["rows"][0]["remote"] == "remote_unavailable"
    assert "--require-remote" in err


def test_require_remote_makes_unavailable_fatal(server, capsys, tmp_path):
    url = server.base + "/gone.bin"

    code, _, _ = _run(
        capsys, "verify", _manifest(tmp_path, url), "--against-remote", "--require-remote"
    )

    assert code == cli.EXIT_FAIL


def test_a_removed_source_is_unavailable_with_the_reason(capsys, tmp_path):
    path = _manifest(tmp_path, "figshare:32519553")

    code, out, _ = _run(capsys, "verify", path, "--against-remote", "--json")

    assert code == cli.EXIT_OK
    assert json.loads(out)["rows"][0]["remote"] == "remote_unavailable"


def test_the_check_time_is_recorded(capsys, tmp_path, monkeypatch):
    from acadfetch import remote

    monkeypatch.setattr(
        remote.registry,
        "resolve",
        lambda _c, _t: Record(
            source="zenodo",
            target="zenodo:1",
            files=[File("https://example/a.bin", "a.bin", 1, "md5:" + MD5.hexdigest())],
        ),
    )
    path = _manifest(
        tmp_path, "zenodo:1", "md5:" + MD5.hexdigest(), file_url="https://example/a.bin"
    )

    code, _, _ = _run(capsys, "verify", path, "--against-remote")

    assert code == cli.EXIT_OK
    written = json.loads(path.read_text("utf-8"))
    assert written["remote_checked_at"]


def test_without_the_flag_nothing_is_written_or_asked(server, capsys, tmp_path):
    url = _served(server)
    path = _manifest(tmp_path, url)
    before = path.read_text("utf-8")

    code, out, _ = _run(capsys, "verify", path, "--json")

    assert code == cli.EXIT_OK
    assert "remote" not in json.loads(out)
    assert path.read_text("utf-8") == before
    assert server.cfg["hits"] == {}, "no request without the flag"


def test_a_zenodo_record_that_changed_is_a_mismatch(monkeypatch, capsys, tmp_path):
    from acadfetch import remote

    monkeypatch.setattr(
        remote.registry,
        "resolve",
        lambda _c, _t: Record(
            source="zenodo",
            target="zenodo:1",
            files=[File("https://example/a.bin", "a.bin", 1, "md5:" + "1" * 32)],
        ),
    )
    path = _manifest(
        tmp_path, "zenodo:1", "md5:" + MD5.hexdigest(), file_url="https://example/a.bin"
    )

    code, out, _ = _run(capsys, "verify", path, "--against-remote", "--json")

    assert json.loads(out)["rows"][0]["remote"] == "remote_mismatch"
    assert code == cli.EXIT_FAIL


def test_judge_normalises_case_and_algorithm():
    from acadfetch import remote

    row = {"url": "u", "checksum": "MD5:" + MD5.hexdigest().upper()}
    assert remote.judge({"u": "md5:" + MD5.hexdigest()}, row, "")[0] == "remote_match"
    assert remote.judge({"u": "md5:" + "2" * 32}, row, "")[0] == "remote_mismatch"


def test_judge_refuses_an_unparseable_side():
    from acadfetch import remote

    status, detail = remote.judge(
        {"u": "md5:" + MD5.hexdigest()}, {"url": "u", "checksum": "nope"}, ""
    )
    assert status == "remote_no_checksum" and "parseable" in detail

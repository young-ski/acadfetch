from __future__ import annotations

import json

import pytest

from acadfetch import cli
from acadfetch.client import Client
from acadfetch.sources.base import File, Record


def _run(capsys, *argv):
    code = cli.main([str(a) for a in argv])
    out, err = capsys.readouterr()
    return code, out, err


def _record(server, count=3, payload=b"P" * 12, size=True):
    files = []
    for i in range(count):
        name = f"{i}.bin"
        server.cfg["files"]["/" + name] = payload
        files.append(File(server.base + "/" + name, name, len(payload) if size else None))
    return Record(source="direct", target="t", files=files)


# -- one bad file must not take the batch ---------------------------------


def test_an_unexpected_worker_error_is_one_failed_row(server, monkeypatch, capsys, tmp_path):
    record = _record(server)
    monkeypatch.setattr(cli.registry, "resolve", lambda _c, _t: record)

    from acadfetch import downloader

    original = downloader._attempt

    def sometimes(client, file, part, meta_path, final, opts, resumed_from):
        if file.name == "1.bin":
            raise PermissionError("disk said no")
        return original(client, file, part, meta_path, final, opts, resumed_from)

    monkeypatch.setattr(downloader, "_attempt", sometimes)

    code, out, _ = _run(capsys, "fetch", "t", "--json", "-o", tmp_path)

    doc = json.loads(out)
    states = [row["state"] for row in doc["files"]]
    assert states.count("downloaded") == 2
    assert states.count("failed") == 1
    assert doc["files"][1]["error"].startswith("PermissionError")
    assert code == cli.EXIT_PARTIAL
    assert (tmp_path / "manifest.json").exists()


def test_a_directory_at_the_final_name_is_one_failed_row(server, monkeypatch, capsys, tmp_path):
    record = _record(server)
    monkeypatch.setattr(cli.registry, "resolve", lambda _c, _t: record)
    (tmp_path / "1.bin").mkdir()

    code, out, err = _run(capsys, "fetch", record.target, "--json", "-o", tmp_path)

    doc = json.loads(out)
    assert code == cli.EXIT_PARTIAL
    assert doc["files"][1]["state"] == "failed"
    assert len(doc["files"]) == 3, "the batch keeps its other rows"
    assert "Traceback" not in err


# -- a truncated API body is retried --------------------------------------


class _Body:
    def __init__(self, read):
        self.headers = {}
        self._read = read

    def read(self, n=None):
        return self._read()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_a_truncated_json_body_is_retried(monkeypatch):
    from http.client import IncompleteRead

    calls = {"n": 0}

    def read():
        calls["n"] += 1
        if calls["n"] == 1:
            raise IncompleteRead(b"")
        return b'{"ok": true}'

    client = Client(rate=1000, retries=2)
    monkeypatch.setattr(client, "_sleep", lambda *a: None)
    monkeypatch.setattr(client, "_open", lambda req: _Body(read))

    assert client.get_json("https://x") == {"ok": True}
    assert calls["n"] == 2


def test_a_body_that_never_arrives_is_one_http_error(monkeypatch):
    from http.client import IncompleteRead

    client = Client(rate=1000, retries=1)
    monkeypatch.setattr(client, "_sleep", lambda *a: None)
    monkeypatch.setattr(
        client, "_open", lambda req: _Body(lambda: (_ for _ in ()).throw(IncompleteRead(b"")))
    )

    with pytest.raises(Exception) as caught:
        client.get_json("https://x")
    assert "could not read the response body" in str(caught.value)


# -- local refusals do not pretend to be HTTP -----------------------------


def test_an_output_path_that_is_a_file_says_so(server, monkeypatch, tmp_path, capsys):
    record = _record(server, count=1)
    monkeypatch.setattr(cli.registry, "resolve", lambda _c, _t: record)
    notadir = tmp_path / "notadir"
    notadir.write_text("x", "utf-8")

    code, out, err = _run(capsys, "fetch", record.target, "-o", notadir)

    assert code == cli.EXIT_FAIL
    assert "-o must be a directory" in err
    assert "Traceback" not in err


def test_a_dry_run_reports_a_shortfall_without_failing(server, monkeypatch, capsys, tmp_path):
    import shutil

    from acadfetch import downloader

    record = _record(server, count=1, payload=b"P" * 5000)
    monkeypatch.setattr(cli.registry, "resolve", lambda _c, _t: record)
    monkeypatch.setattr(
        downloader.shutil, "disk_usage", lambda _p: shutil._ntuple_diskusage(0, 0, 10)
    )

    code, out, _ = _run(capsys, "fetch", record.target, "-n", "--json", "-o", tmp_path)

    doc = json.loads(out)
    assert code == cli.EXIT_OK
    assert doc["disk"]["ok"] is False
    assert doc["files"][0]["state"] == "planned"


def test_max_size_is_a_local_refusal_not_http_zero(server, monkeypatch, capsys, tmp_path):
    record = _record(server, count=1, payload=b"P" * 500, size=False)
    monkeypatch.setattr(cli.registry, "resolve", lambda _c, _t: record)

    code, out, _ = _run(
        capsys, "fetch", record.target, "--json", "-o", tmp_path, "--max-size", "10"
    )

    doc = json.loads(out)
    assert code == cli.EXIT_FAIL
    assert "refused locally" in doc["files"][0]["error"]
    assert "HTTP 0" not in doc["files"][0]["error"]

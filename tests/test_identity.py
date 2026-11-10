from __future__ import annotations

import json

import pytest

from acadfetch import cli
from acadfetch.errors import TargetError
from acadfetch.sources import doi


def _run(capsys, *argv):
    code = cli.main([str(a) for a in argv])
    out, err = capsys.readouterr()
    return code, out, err


def _record(server, name="data.csv", payload=b"AAAA", **kw):
    from acadfetch.sources.base import File, Record

    url = server.base + "/" + name
    server.cfg["files"]["/" + name] = payload
    return Record(
        source="direct", target=url, files=[File(url, name, kw.get("size", len(payload)))]
    )


# -- a DOI that resolves to a web page ------------------------------------


class LandingClient:
    def __init__(self, content_type: str):
        self.content_type = content_type

    def get_json(self, url):
        return {
            "responseCode": 1,
            "values": [{"type": "URL", "data": {"value": "https://data.example/landing"}}],
        }

    def head(self, url):
        return {"Content-Type": self.content_type}


@pytest.mark.parametrize("ctype", ["text/html; charset=utf-8", "application/xhtml+xml"])
def test_a_landing_page_is_refused(ctype):
    with pytest.raises(TargetError) as caught:
        doi.resolve(LandingClient(ctype), "10.17632/z8g47vzt4p.1")
    assert "web page, not a data file" in str(caught.value)
    assert "https://data.example/landing" in str(caught.value)


class PdfClient(LandingClient):
    """A resolved URL that really is a file."""

    def __init__(self):
        super().__init__("application/pdf")

    def head(self, url):
        return {"Content-Type": "application/pdf", "Content-Length": "4"}


class SilentClient(LandingClient):
    """A server that answers HEAD with no headers at all, like Mendeley."""

    def __init__(self, body: bytes):
        super().__init__("")
        self.body = body

    def head(self, url):
        return {}

    def open(self, url, headers=None, attempts=None):
        body = self.body

        class Resp:
            headers = {"Content-Type": ""}

            def read(self, _n=None):
                return body

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        return Resp()


def test_markup_from_a_silent_server_is_refused():
    """No Content-Type is not a pass: look at the first bytes."""
    with pytest.raises(TargetError, match="web page, not a data file"):
        doi.resolve(SilentClient(b"<!DOCTYPE html><html>"), "10.17632/x.1")


def test_binary_from_a_silent_server_passes():
    record = doi.resolve(SilentClient(b"\x89PNG\r\n"), "10.17632/x.1")
    assert record.doi == "10.17632/x.1"


def test_the_doi_is_kept_as_the_identity():
    record = doi.resolve(PdfClient(), "10.17632/x.1")
    assert record.doi == "10.17632/x.1"
    assert record.target == "https://data.example/landing"


def test_a_non_html_landing_url_still_dispatches():
    from acadfetch.sources.base import Record

    record = doi.resolve(PdfClient(), "10.17632/x.1")
    assert isinstance(record, Record)
    assert record.source == "direct"
    assert record.files[0].size == 4


# -- the default directory identifies the target --------------------------


def test_two_hosts_with_one_basename_get_two_directories(
    server, make_server, monkeypatch, capsys, tmp_path
):
    first, second = server, make_server()
    first.cfg["files"]["/data.csv"] = b"AAAA"
    second.cfg["files"]["/data.csv"] = b"BBBB"
    monkeypatch.chdir(tmp_path)

    _run(capsys, "fetch", first.base + "/data.csv", "--json", "-q")
    _run(capsys, "fetch", second.base + "/data.csv", "--json", "-q")

    files = sorted(tmp_path.glob("acadfetch-out/*/data.csv"))
    assert len(files) == 2, "one directory per host"
    assert sorted(f.read_bytes() for f in files) == [b"AAAA", b"BBBB"]


# -- when may an existing file stand in? ----------------------------------


def test_a_manifest_from_the_same_url_is_trusted(server, monkeypatch, capsys, tmp_path):
    record = _record(server)
    monkeypatch.setattr(cli.registry, "resolve", lambda _c, _t: record)
    target = record.target

    _run(capsys, "fetch", target, "-q", "-o", tmp_path)
    _run(capsys, "fetch", target, "-q", "-o", tmp_path)

    assert server.cfg["hits"]["/data.csv"] == 1


def test_without_a_manifest_or_explicit_out_the_file_is_fetched_again(
    server, monkeypatch, capsys, tmp_path
):
    """A size match alone is not proof: a different dataset can be the same size."""
    record = _record(server)
    monkeypatch.setattr(cli.registry, "resolve", lambda _c, _t: record)
    monkeypatch.chdir(tmp_path)

    _run(capsys, "fetch", record.target, "-q")
    (tmp_path / "acadfetch-out").glob("*/manifest.json").__next__().unlink()
    _run(capsys, "fetch", record.target, "-q")

    assert server.cfg["hits"]["/data.csv"] == 2


def test_an_explicit_output_directory_is_trusted(server, monkeypatch, capsys, tmp_path):
    record = _record(server)
    monkeypatch.setattr(cli.registry, "resolve", lambda _c, _t: record)

    _run(capsys, "fetch", record.target, "-q", "-o", tmp_path)
    (tmp_path / "manifest.json").unlink()
    _run(capsys, "fetch", record.target, "-q", "-o", tmp_path)

    assert server.cfg["hits"]["/data.csv"] == 1


def test_force_fetches_again(server, monkeypatch, capsys, tmp_path):
    record = _record(server)
    monkeypatch.setattr(cli.registry, "resolve", lambda _c, _t: record)

    _run(capsys, "fetch", record.target, "-q", "-o", tmp_path)
    code, _, _ = _run(capsys, "fetch", record.target, "-q", "-o", tmp_path, "--force")

    assert code == cli.EXIT_OK
    assert server.cfg["hits"]["/data.csv"] == 2


def test_a_checksum_match_is_still_proof(server, monkeypatch, capsys, tmp_path):
    import hashlib

    record = _record(server)
    record.files[0].checksum = "sha256:" + hashlib.sha256(b"AAAA").hexdigest()
    monkeypatch.setattr(cli.registry, "resolve", lambda _c, _t: record)
    monkeypatch.chdir(tmp_path)

    _run(capsys, "fetch", record.target, "-q")
    # No manifest, no -o: the checksum alone has to be enough.
    (tmp_path / "acadfetch-out").glob("*/manifest.json").__next__().unlink()
    code, _, _ = _run(capsys, "fetch", record.target, "-q")

    assert code == cli.EXIT_OK
    assert server.cfg["hits"]["/data.csv"] == 1


def test_the_plan_is_still_written_without_fetching(server, monkeypatch, capsys, tmp_path):
    record = _record(server)
    monkeypatch.setattr(cli.registry, "resolve", lambda _c, _t: record)

    code, out, _ = _run(capsys, "fetch", record.target, "-n", "--json", "-o", tmp_path)

    assert code == cli.EXIT_OK
    assert json.loads(out)["files"][0]["state"] == "planned"
    assert not (tmp_path / "data.csv").exists()

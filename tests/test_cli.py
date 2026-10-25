from __future__ import annotations

import argparse

import pytest

from acadfetch import cli


@pytest.mark.parametrize(
    "text,expected",
    [("10", 10), ("500B", 500), ("1KB", 1024), ("1.5MiB", 1572864), ("2G", 2 << 30)],
)
def test_parse_size(text, expected):
    assert cli.parse_size(text) == expected


@pytest.mark.parametrize("text", ["", "abc", "10X", "-5"])
def test_parse_size_rejects(text):
    with pytest.raises(argparse.ArgumentTypeError):
        cli.parse_size(text)


def test_version_exits_zero(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    assert "acadfetch" in capsys.readouterr().out


def test_sources_lists_adapters(capsys):
    assert cli.main(["sources"]) == 0
    out = capsys.readouterr().out
    assert "zenodo" in out and "doi" in out


def test_bad_target_exit_1(capsys):
    assert cli.main(["manifest", "definitely not a target"]) == 1
    assert "cannot tell what" in capsys.readouterr().err


def test_headers_parse():
    assert cli._headers(["A: 1", "b:x:y"]) == {"A": "1", "b": "x:y"}
    assert cli._headers(None) == {}


def test_header_without_colon_exits_1(capsys):
    assert cli.main(["manifest", "zenodo:1", "--header", "oops"]) == 1
    assert "--header needs" in capsys.readouterr().err


def test_client_carries_headers():
    args = cli.build_parser().parse_args(["fetch", "zenodo:1", "--header", "X: y"])
    assert cli._client(args).headers == {"X": "y"}


def _stub_resolve(monkeypatch, source="zenodo"):
    from acadfetch.sources import registry
    from acadfetch.sources.base import File, Record

    def fake(client, target, allow_doi=True):
        client.source = source
        return Record(source=source, target=target, files=[File("https://x/f", "f")], doi="10.1/x")

    monkeypatch.setattr(registry, "resolve", fake)


def test_env_token_is_reported_redacted(monkeypatch, capsys, tmp_path):
    _stub_resolve(monkeypatch)
    monkeypatch.setenv("ZENODO_TOKEN", "tok-abcd-9999")
    code = cli.main(["manifest", "zenodo:1", "-o", str(tmp_path / "m.json")])
    out = capsys.readouterr().out
    assert code == 0
    assert "token from $ZENODO_TOKEN (…9999)" in out
    assert "tok-abcd-9999" not in out
    assert '"hint": "…9999"' in (tmp_path / "m.json").read_text("utf-8")


def test_no_token_skips_env_token(monkeypatch, capsys, tmp_path):
    _stub_resolve(monkeypatch)
    monkeypatch.setenv("ZENODO_TOKEN", "tok-abcd-9999")
    assert cli.main(["manifest", "zenodo:1", "-o", str(tmp_path / "m.json"), "--no-token"]) == 0
    assert "token from" not in capsys.readouterr().out


def test_token_only_loaded_for_its_own_source(monkeypatch):
    _stub_resolve(monkeypatch, source="arxiv")
    monkeypatch.setenv("ZENODO_TOKEN", "tok-abcd-9999")
    args = cli.build_parser().parse_args(["manifest", "arxiv:2401.1"])
    client = cli._client(args)
    client.source = "arxiv"
    assert client._request("https://arxiv.org/x").get_header("Authorization") is None
    client.source = "zenodo"
    assert client._request("https://zenodo.org/x").get_header("Authorization") is not None


def test_fully_skipped_run_prints_no_empty_progress_frame(monkeypatch, capsys, tmp_path):
    """The re-run case: nothing moves, so "0 file(s) 0 B" would contradict the tally."""
    _stub_resolve(monkeypatch)
    out = tmp_path / "data"
    out.mkdir()
    (out / "f").write_bytes(b"payload")
    assert cli.main(["fetch", "zenodo:1", "-o", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "skip f" in printed
    assert "0 file(s)" not in printed
    assert "1/1 file(s)" in printed

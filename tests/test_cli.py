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

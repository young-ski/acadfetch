from __future__ import annotations

import json

import pytest

from acadfetch import cli
from acadfetch.sources.base import File, Record


def _run(capsys, *argv):
    code = cli.main([str(a) for a in argv])
    out, err = capsys.readouterr()
    return code, out, err


@pytest.fixture
def two(server, monkeypatch):
    server.cfg["files"]["/a.pdf"] = b"PDF"
    server.cfg["files"]["/b.csv"] = b"CSV"
    record = Record(
        source="direct",
        target="t",
        files=[File(server.base + "/a.pdf", "a.pdf", 3), File(server.base + "/b.csv", "b.csv", 3)],
    )
    monkeypatch.setattr(cli.registry, "resolve", lambda _c, _t: record)
    return record


def test_glob_narrows_the_plan(two, capsys):
    code, out, _ = _run(capsys, "fetch", "t", "-n", "--json", "--glob", "*.pdf")

    assert code == cli.EXIT_OK
    assert [row["name"] for row in json.loads(out)["files"]] == ["a.pdf"]


def test_glob_and_only_are_both_applied(two, capsys):
    code, _, err = _run(capsys, "fetch", "t", "-n", "--glob", "*.pdf", "--only", "b.csv")

    assert code == cli.EXIT_FAIL
    assert "no file matched" in err


def test_a_glob_that_matches_nothing_names_the_candidates(two, capsys):
    code, _, err = _run(capsys, "fetch", "t", "-n", "--glob", "*.zip")

    assert code == cli.EXIT_FAIL
    assert "--glob *.zip" in err
    assert "a.pdf" in err


def test_glob_only_downloads_the_match(two, server, capsys, tmp_path):
    code, _, _ = _run(capsys, "fetch", "t", "-q", "--glob", "*.csv", "-o", tmp_path)

    assert code == cli.EXIT_OK
    assert (tmp_path / "b.csv").exists()
    assert not (tmp_path / "a.pdf").exists()


def test_urls_to_stdout_are_aria2_shaped(two, capsys, tmp_path):
    code, out, _ = _run(capsys, "fetch", "t", "-w", "-", "-o", tmp_path)

    assert code == cli.EXIT_OK
    lines = out.splitlines()
    assert lines[0].endswith("/a.pdf")
    assert lines[1] == "  out=a.pdf"
    assert lines[2] == f"  dir={tmp_path}"
    assert "out=b.csv" in out


def test_urls_to_a_file(two, capsys, tmp_path):
    target = tmp_path / "list.txt"

    code, out, err = _run(capsys, "fetch", "t", "-w", target, "-o", tmp_path)

    assert code == cli.EXIT_OK
    assert out == ""
    assert "2 url(s)" in err
    assert "out=a.pdf" in target.read_text("utf-8")


def test_urls_honour_glob(two, capsys, tmp_path):
    code, out, _ = _run(capsys, "fetch", "t", "-w", "-", "--glob", "*.pdf", "-o", tmp_path)

    assert code == cli.EXIT_OK
    assert "a.pdf" in out and "b.csv" not in out


def test_urls_and_json_are_mutually_exclusive(two, capsys):
    code, _, err = _run(capsys, "fetch", "t", "-w", "-", "--json")

    assert code == cli.EXIT_FAIL
    assert "drop --json" in err


def test_no_bytes_are_fetched_for_a_list(two, server, capsys, tmp_path):
    _run(capsys, "fetch", "t", "-w", "-", "-o", tmp_path)

    assert server.cfg["hits"] == {}, "a list must not transfer"

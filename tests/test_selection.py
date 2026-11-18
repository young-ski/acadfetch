"""Selecting files from one record, and keeping the misses.

Typing 70 names on a command line is the failure this fixes: a name list goes in,
the files that could not be fetched come back verbatim, and the same file feeds
the retry. A typo is a visible failure, never a quietly smaller download.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from acadfetch import cli
from acadfetch.sources import registry
from acadfetch.sources.base import File, Record, Result


@pytest.fixture
def record(server, monkeypatch):
    """One record with four files, all served locally."""
    for name in ("a.bin", "b.bin", "c.bin", "d.bin"):
        server.cfg["files"]["/" + name] = name.encode()

    def resolve(client, target):
        client.source = "zenodo"
        return Record(
            source="zenodo",
            target=target,
            files=[
                File(server.base + "/" + name, name, 5)
                for name in ("a.bin", "b.bin", "c.bin", "d.bin")
            ],
        )

    monkeypatch.setattr(registry, "resolve", resolve)
    return server


def _selection(tmp_path: Path, *names: str) -> Path:
    path = tmp_path / "want.txt"
    path.write_text("# a note\n\n" + "\n".join(names) + "\n", "utf-8")
    return path


def _run(capsys, *argv):
    code = cli.main([str(a) for a in argv])
    out, err = capsys.readouterr()
    return code, out, err


def _downloaded(root: Path) -> set[str]:
    return {path.name for path in root.rglob("*") if path.is_file()}


def test_only_file_downloads_just_the_listed_names(record, capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    selection = _selection(tmp_path, "b.bin", "d.bin")

    code, _, _ = _run(
        capsys, "fetch", "zenodo:1", "--only-file", selection, "--failed-out", "failed.txt", "-q"
    )

    assert code == cli.EXIT_OK
    fetched = _downloaded(tmp_path / "acadfetch-out")
    assert {"b.bin", "d.bin"} <= fetched
    assert "a.bin" not in fetched and "c.bin" not in fetched
    assert (tmp_path / "failed.txt").read_text("utf-8") == ""


def test_only_and_only_file_combine(record, capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    selection = _selection(tmp_path, "a.bin")

    code, _, _ = _run(
        capsys,
        "fetch",
        "zenodo:1",
        "--only-file",
        selection,
        "--only",
        "c.bin",
        "--failed-out",
        "failed.txt",
        "-q",
    )

    assert code == cli.EXIT_OK
    assert {"a.bin", "c.bin"} <= _downloaded(tmp_path / "acadfetch-out")


def test_only_file_combines_with_glob(record, capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    selection = _selection(tmp_path, "a.bin", "b.bin")

    assert (
        _run(
            capsys,
            "fetch",
            "zenodo:1",
            "--only-file",
            selection,
            "--glob",
            "b*",
            "--failed-out",
            "failed.txt",
            "-q",
        )[0]
        == cli.EXIT_OK
    )
    assert "b.bin" in _downloaded(tmp_path / "acadfetch-out")
    assert "a.bin" not in _downloaded(tmp_path / "acadfetch-out")


def test_names_are_deduplicated_case_insensitively(record, capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    selection = _selection(tmp_path, "a.bin", "A.BIN", "a.bin")

    code, out, _ = _run(
        capsys, "fetch", "zenodo:1", "--only-file", selection, "--failed-out", "failed.txt", "-q"
    )

    assert code == cli.EXIT_OK
    assert out.count("ok   a.bin") == 1


def test_a_failed_file_lands_in_the_list_and_the_rest_continues(
    record, capsys, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    del record.cfg["files"]["/b.bin"]  # 404
    selection = _selection(tmp_path, "a.bin", "b.bin")

    code, out, _ = _run(
        capsys, "fetch", "zenodo:1", "--only-file", selection, "--failed-out", "failed.txt", "-q"
    )

    assert code == cli.EXIT_PARTIAL
    assert (tmp_path / "failed.txt").read_text("utf-8") == "b.bin\n"
    assert "a.bin" in _downloaded(tmp_path / "acadfetch-out")
    assert "FAIL b.bin" in out


def test_a_typo_is_a_failure_not_a_shorter_download(record, capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    selection = _selection(tmp_path, "a.bin", "typo.bin")

    code, _, err = _run(
        capsys, "fetch", "zenodo:1", "--only-file", selection, "--failed-out", "failed.txt", "-q"
    )

    assert code == cli.EXIT_PARTIAL
    assert "1 selected name(s) matched nothing: typo.bin" in err
    assert (tmp_path / "failed.txt").read_text("utf-8") == "typo.bin\n"


def test_a_selection_that_matches_nothing_is_exit_1(record, capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    selection = _selection(tmp_path, "typo.bin", "other.bin")

    code, _, err = _run(
        capsys, "fetch", "zenodo:1", "--only-file", selection, "--failed-out", "failed.txt", "-q"
    )

    assert code == cli.EXIT_FAIL
    assert "no file matched" in err
    assert "available: a.bin" in err
    assert (tmp_path / "failed.txt").read_text("utf-8") == "typo.bin\nother.bin\n"


def test_the_failed_list_is_the_retry_input(record, capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    del record.cfg["files"]["/b.bin"]
    selection = _selection(tmp_path, "a.bin", "b.bin")
    assert (
        _run(
            capsys,
            "fetch",
            "zenodo:1",
            "--only-file",
            selection,
            "--failed-out",
            "failed.txt",
            "-q",
        )[0]
        == cli.EXIT_PARTIAL
    )

    record.cfg["files"]["/b.bin"] = b"b.bin"
    failed = tmp_path / "failed.txt"
    code, _, _ = _run(
        capsys, "fetch", "zenodo:1", "--only-file", failed, "--failed-out", "failed.txt", "-q"
    )

    assert code == cli.EXIT_OK
    assert failed.read_text("utf-8") == ""


def test_list_files_prints_names_and_moves_nothing(record, capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    code, out, _ = _run(capsys, "fetch", "zenodo:1", "--list-files")

    assert code == cli.EXIT_OK
    assert out.split() == ["a.bin", "b.bin", "c.bin", "d.bin"]
    assert not (tmp_path / "acadfetch-out").exists()


def test_list_files_refuses_json(record, capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    code, _, err = _run(capsys, "fetch", "zenodo:1", "--list-files", "--json")
    assert code == cli.EXIT_FAIL
    assert "drop --json" in err


def test_only_file_reads_stdin(record, capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.stdin", io.StringIO("b.bin\n# comment\n"))

    code, _, _ = _run(
        capsys, "fetch", "zenodo:1", "--only-file", "-", "--failed-out", "failed.txt", "-q"
    )

    assert code == cli.EXIT_OK
    assert "b.bin" in _downloaded(tmp_path / "acadfetch-out")


def test_a_missing_selection_file_names_itself(record, capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    code, _, err = _run(capsys, "fetch", "zenodo:1", "--only-file", "nope.txt")
    assert code == cli.EXIT_FAIL
    assert "no such list file" in err and "nope.txt" in err


def test_json_reports_unmatched_names(record, capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    selection = _selection(tmp_path, "a.bin", "typo.bin")

    code, out, _ = _run(
        capsys,
        "fetch",
        "zenodo:1",
        "--only-file",
        selection,
        "--json",
        "--failed-out",
        "failed.txt",
    )

    doc = json.loads(out)
    assert code == cli.EXIT_PARTIAL
    assert doc["unmatched"] == ["typo.bin"]
    assert doc["summary"]["unmatched"] == 1
    assert doc["summary"]["ok"] is False
    assert [f["name"] for f in doc["files"]] == ["a.bin"]


def test_json_refuses_a_stdout_failure_list(record, capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    selection = _selection(tmp_path, "a.bin")
    code, out, err = _run(
        capsys, "fetch", "zenodo:1", "--only-file", selection, "--json", "--failed-out", "-"
    )
    assert code == cli.EXIT_FAIL
    assert "claims stdout" in json.loads(out)["error"]
    assert "claims stdout" in err


def test_an_interrupt_puts_the_rest_of_the_selection_on_the_list(
    record, capsys, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    selection = _selection(tmp_path, "a.bin", "b.bin", "c.bin")

    def fake_fetch(rec, opts, client, only=None, globs=None):
        opts.interrupted = True
        return [Result(rec.files[1], "failed", error="interrupted")]

    monkeypatch.setattr(cli, "fetch_record", fake_fetch)

    code, _, _ = _run(
        capsys, "fetch", "zenodo:1", "--only-file", selection, "--failed-out", "failed.txt", "-q"
    )

    assert code == cli.EXIT_INTERRUPT
    lines = (tmp_path / "failed.txt").read_text("utf-8").splitlines()
    assert lines == ["a.bin", "b.bin", "c.bin"]


def test_failed_out_is_optional(record, capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    selection = _selection(tmp_path, "a.bin")
    assert _run(capsys, "fetch", "zenodo:1", "--only-file", selection, "-q")[0] == cli.EXIT_OK
    assert not (tmp_path / "failed.txt").exists()


def test_urls_are_filtered_by_the_selection(record, capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    selection = _selection(tmp_path, "b.bin")

    code, out, _ = _run(capsys, "fetch", "zenodo:1", "--only-file", selection, "-w", "-")

    assert code == cli.EXIT_OK
    assert record.base + "/b.bin" in out
    assert record.base + "/a.bin" not in out


def test_batch_accepts_a_selection_too(record, capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    selection = _selection(tmp_path, "a.bin")
    listing = tmp_path / "identifiers.txt"
    listing.write_text("zenodo:1\n", "utf-8")

    code, _, _ = _run(capsys, "batch", listing, "--only-file", selection, "-q")

    assert code == cli.EXIT_OK
    assert "a.bin" in _downloaded(tmp_path / "acadfetch-out")
    assert (tmp_path / "failed.txt").read_text("utf-8") == ""

"""``batch``: one list in, one failure list out.

The command exists so a hundred identifiers is one command, not a shell loop
that loses the failures. These tests hold it to the two things that make that
true: a bad line never stops the run, and the failed lines land in a file that
can be handed straight back.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from acadfetch import cli
from acadfetch.sources import registry
from acadfetch.sources.base import File, Record


@pytest.fixture
def served(server):
    server.cfg["files"]["/a.bin"] = b"alpha payload"
    server.cfg["files"]["/b.bin"] = b"beta"
    return server


def _list(tmp_path: Path, *lines: str) -> Path:
    path = tmp_path / "identifiers.txt"
    path.write_text("\n".join(lines) + "\n", "utf-8")
    return path


def _run(capsys, *argv):
    code = cli.main([str(a) for a in argv])
    out, err = capsys.readouterr()
    return code, out, err


def _downloaded(root: Path) -> set[str]:
    return {path.name for path in root.rglob("*") if path.is_file()}


def test_downloads_every_identifier_and_replaces_the_failed_list(
    served, capsys, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "failed.txt").write_text("stale-from-last-time\n", "utf-8")
    listing = _list(tmp_path, served.base + "/a.bin", served.base + "/b.bin")

    code, out, _ = _run(capsys, "batch", listing, "-q")

    assert code == cli.EXIT_OK
    assert "2/2 identifier(s) ok" in out
    assert (tmp_path / "failed.txt").read_text("utf-8") == ""
    assert {"a.bin", "b.bin"} <= _downloaded(tmp_path / "acadfetch-out")


def test_a_failed_identifier_is_listed_and_the_run_goes_on(served, capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    good = served.base + "/a.bin"
    bad = served.base + "/missing.bin"
    listing = _list(tmp_path, good, bad)

    code, out, err = _run(capsys, "batch", listing, "-q")

    assert code == cli.EXIT_PARTIAL
    assert (tmp_path / "failed.txt").read_text("utf-8") == bad + "\n"
    assert "a.bin" in _downloaded(tmp_path / "acadfetch-out")
    assert "line 2" in out and "missing.bin" in out
    assert "missing.bin" in err
    assert "1/2 identifier(s) ok, 1 failed" in out


def test_all_failures_are_exit_1(served, capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    listing = _list(tmp_path, served.base + "/nope1", served.base + "/nope2")

    code, _, _ = _run(capsys, "batch", listing, "-q")

    assert code == cli.EXIT_FAIL
    assert (tmp_path / "failed.txt").read_text("utf-8").count("\n") == 2


def test_comments_blank_lines_bom_and_crlf_are_skipped(served, capsys, tmp_path, monkeypatch):
    """A hand-edited list is the normal case, including one saved by Notepad."""
    monkeypatch.chdir(tmp_path)
    url = served.base + "/a.bin"
    raw = "\ufeff# records for the thesis\n\n  " + url + "  \r\n# and a note\r\n"
    listing = tmp_path / "identifiers.txt"
    listing.write_bytes(raw.encode("utf-8"))

    code, _, _ = _run(capsys, "batch", listing, "-q", "--json")

    assert code == cli.EXIT_OK
    assert (tmp_path / "failed.txt").read_text("utf-8") == ""


def test_an_empty_list_is_exit_1_and_says_so(capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    listing = _list(tmp_path, "# nothing yet")

    code, _, err = _run(capsys, "batch", listing)

    assert code == cli.EXIT_FAIL
    assert "lists no identifiers" in err
    assert (tmp_path / "failed.txt").exists()
    assert (tmp_path / "failed.txt").read_text("utf-8") == ""


def test_a_missing_list_is_one_line(capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    code, _, err = _run(capsys, "batch", tmp_path / "nope.txt")
    assert code == cli.EXIT_FAIL
    assert "nope.txt" in err
    assert "Traceback" not in err


def test_failed_list_is_a_working_retry_input(served, capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    listing = _list(tmp_path, served.base + "/a.bin", served.base + "/missing.bin")
    assert _run(capsys, "batch", listing, "-q")[0] == cli.EXIT_PARTIAL
    retry = tmp_path / "failed.txt"

    # The server now has the file; the list written last time is the input.
    served.cfg["files"]["/missing.bin"] = b"now here"
    code, _, _ = _run(capsys, "batch", retry, "-q")

    assert code == cli.EXIT_OK
    assert retry.read_text("utf-8") == ""


def test_each_identifier_keeps_its_own_directory(served, capsys, tmp_path, monkeypatch):
    """Two files can share a name; the leaf directory is what keeps them apart."""
    monkeypatch.chdir(tmp_path)
    served.cfg["files"]["/one/same.bin"] = b"one"
    served.cfg["files"]["/two/same.bin"] = b"two"
    listing = _list(tmp_path, served.base + "/one/same.bin", served.base + "/two/same.bin")

    assert _run(capsys, "batch", listing, "-q", "-o", tmp_path / "data")[0] == cli.EXIT_OK

    leaves = sorted(p.parent for p in (tmp_path / "data").rglob("same.bin"))
    assert len(leaves) == 2
    assert sorted(p.read_bytes() for p in (tmp_path / "data").rglob("same.bin")) == [b"one", b"two"]


def test_the_failed_list_keeps_the_identifier_verbatim(served, capsys, tmp_path, monkeypatch):
    """A redacted list cannot be replayed; the failure file must not be redacted."""
    monkeypatch.chdir(tmp_path)
    url = served.base + "/missing.bin?token=shh"
    listing = _list(tmp_path, url)

    assert _run(capsys, "batch", listing, "-q")[0] == cli.EXIT_FAIL
    assert (tmp_path / "failed.txt").read_text("utf-8") == url + "\n"


def test_only_does_not_eat_the_list(served, capsys, tmp_path, monkeypatch):
    """``--only`` is nargs=+; the fetch parser's recovery must not fire here."""
    monkeypatch.chdir(tmp_path)
    listing = _list(tmp_path, served.base + "/a.bin")
    code, _, _ = _run(capsys, "batch", listing, "-q", "--only", "a.bin")
    assert code == cli.EXIT_OK


def test_json_is_one_document_with_a_row_per_identifier(served, capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    good = served.base + "/a.bin"
    bad = served.base + "/missing.bin"
    listing = _list(tmp_path, good, bad)

    code, out, err = _run(capsys, "batch", listing, "-q", "--json")

    doc = json.loads(out)  # all of stdout parses
    assert code == cli.EXIT_PARTIAL
    assert doc["command"] == "batch"
    assert doc["failed_out"] == "failed.txt"
    assert [row["line"] for row in doc["items"]] == [1, 2]
    assert [row["ok"] for row in doc["items"]] == [True, False]
    assert doc["items"][1]["error"]
    assert doc["items"][0]["report"]["command"] == "fetch"
    assert doc["summary"]["failed"] == 1
    assert doc["summary"]["ok_run"] is False
    assert "missing.bin" in err  # the per-identifier error still reaches stderr


def test_json_and_a_stdout_failed_list_are_refused(served, capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    listing = _list(tmp_path, served.base + "/a.bin")
    code, out, err = _run(capsys, "batch", listing, "-q", "--json", "--failed-out", "-")
    assert code == cli.EXIT_FAIL
    assert "claims stdout" in json.loads(out)["error"]
    assert "claims stdout" in err


def test_interrupt_lists_the_current_and_the_unattempted_lines(monkeypatch, capsys, tmp_path):
    """Rerunning the failure file must cover what the interrupt skipped."""
    monkeypatch.chdir(tmp_path)

    def fake_resolve(client, target):
        client.source = "zenodo"
        return Record(source="zenodo", target=target, files=[File("https://x/" + target, target)])

    calls: list[str] = []

    def fake_fetch(record, opts, client, only=None, globs=None):
        calls.append(record.target)
        if record.target == "two":
            raise KeyboardInterrupt
        return []

    monkeypatch.setattr(registry, "resolve", fake_resolve)
    monkeypatch.setattr(cli, "fetch_record", fake_fetch)
    listing = _list(tmp_path, "one", "two", "three")

    code = cli.main(["batch", str(listing), "-q", "-o", str(tmp_path / "out")])

    assert code == cli.EXIT_INTERRUPT
    assert calls == ["one", "two"]
    assert (tmp_path / "failed.txt").read_text("utf-8") == "two\nthree\n"


def test_a_filter_matching_nothing_is_that_identifier_failing(
    served, capsys, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    listing = _list(tmp_path, served.base + "/a.bin", served.base + "/b.bin")

    code, _, err = _run(capsys, "batch", listing, "-q", "--only", "nope")

    assert code == cli.EXIT_FAIL
    assert err.count("no file matched") == 2
    assert (tmp_path / "failed.txt").read_text("utf-8").count("\n") == 2


def test_failed_out_creates_the_directory(served, capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    listing = _list(tmp_path, served.base + "/missing.bin")

    assert _run(capsys, "batch", listing, "-q", "--failed-out", "logs/failed.txt")[0] == (
        cli.EXIT_FAIL
    )
    assert "missing.bin" in (tmp_path / "logs" / "failed.txt").read_text("utf-8")


def test_failed_out_dash_puts_the_list_on_stdout(served, capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    bad = served.base + "/missing.bin"
    listing = _list(tmp_path, bad)

    code, out, _ = _run(capsys, "batch", listing, "-q", "--failed-out", "-")

    assert code == cli.EXIT_FAIL
    assert bad in out.splitlines(), "the list is the last thing printed"
    assert not (tmp_path / "failed.txt").exists()


def test_out_base_is_used_for_every_identifier(served, capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    listing = _list(tmp_path, served.base + "/a.bin")
    assert _run(capsys, "batch", listing, "-q", "-o", tmp_path / "data")[0] == cli.EXIT_OK
    assert list((tmp_path / "data").glob("direct-*/a.bin"))
    assert not (tmp_path / "acadfetch-out").exists()

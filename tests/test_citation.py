from __future__ import annotations

from pathlib import Path

from acadfetch import cli


def test_the_citation_file_is_cff():
    text = Path("CITATION.cff").read_text("utf-8")
    assert text.startswith("cff-version: 1.2.0")
    assert "type: software" in text
    assert "license: MIT" in text
    assert "https://github.com/young-ski/acadfetch" in text


def test_cite_prints_the_tool_citation(capsys):
    assert cli.main(["--cite"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "acadfetch (version" in out
    assert "manifest.json" in out


def test_cite_needs_no_subcommand(capsys):
    """argparse would otherwise demand one."""
    assert cli.main(["--cite"]) == cli.EXIT_OK
    assert "To cite" in capsys.readouterr().out

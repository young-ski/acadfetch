"""The documentation's claims, as tests.

The failure this project actually has is not a broken feature; it is a document
that goes on describing last quarter's behaviour. Each test below turns one
class of claim into something a build can disagree with, so the next drift is a
red run rather than a reader's bad afternoon.

Deliberately offline and deterministic: no network, no subprocess, no timing.
Only user-facing documents are scanned — the planning and audit documents under
``docs/`` describe what was *proposed*, not what ships.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from acadfetch import cli, consts, manifest, verify

ROOT = Path(__file__).resolve().parent.parent
#: What a user may be told to run. Planning docs (`OPTIMIZATION-PLAN.md`,
#: `plan-0.2.0.md`) and the maintainer runbooks are deliberately excluded: they
#: name flags that were considered and never shipped.
USER_DOCS = (
    ROOT / "README.md",
    ROOT / "CONTRIBUTING.md",
    ROOT / "docs" / "README.md",
    ROOT / "docs" / "demo.md",
    ROOT / "docs" / "integrations.md",
    ROOT / "docs" / "troubleshooting.md",
    ROOT / "docs" / "manifest.md",
    ROOT / "docs" / "json-report.md",
    ROOT / "docs" / "migration-0.2.md",
    ROOT / "docs" / "releasing.md",
)
#: A long flag as prose writes it, not as `--x` inside a longer word.
_MENTION = re.compile(r"(?<![\w-])(--[a-z][a-z0-9-]+)(?![\w-])")


def _long_flags(parser: argparse.ArgumentParser) -> set[str]:
    """Every ``--flag`` any subcommand accepts."""
    flags: set[str] = set()
    for action in parser._actions:
        flags.update(o for o in action.option_strings if o.startswith("--"))
        if isinstance(action, argparse._SubParsersAction):
            for sub in action.choices.values():
                flags |= _long_flags(sub)
    return flags


def _invocations(text: str) -> list[str]:
    """Lines that ask *this* tool to do something.

    Anchoring on the program name is what keeps ``ruff format --check`` and
    ``pytest --cov-fail-under`` out of the comparison.
    """
    found = []
    for line in text.splitlines():
        stripped = line.strip().lstrip("$").strip()
        if stripped.startswith(("acadfetch ", "uvx acadfetch ")):
            found.append(stripped)
    return found


def test_every_flag_the_docs_invoke_exists():
    """A command in the docs that the parser rejects is a broken promise."""
    known = _long_flags(cli.build_parser())
    unknown: dict[str, list[str]] = {}
    for path in USER_DOCS:
        for line in _invocations(path.read_text("utf-8")):
            for flag in _MENTION.findall(line):
                if flag not in known:
                    unknown.setdefault(flag, []).append(f"{path.name}: {line}")
    assert not unknown, f"documented commands use flags that do not exist: {unknown}"


def test_every_flag_is_documented():
    """A flag nobody can find in the docs only exists in `--help`."""
    text = "\n".join(path.read_text("utf-8") for path in USER_DOCS)
    missing = sorted(
        flag
        for flag in _long_flags(cli.build_parser())
        if flag != "--help" and not re.search(re.escape(flag) + r"(?![\w-])", text)
    )
    assert not missing, f"flags with no line in the docs: {missing}"


def _reasons_the_code_can_emit() -> set[str]:
    """Ask the function, rather than restating it: one call per branch."""
    checksums = (None, "md5:" + "0" * 32, "sha256:" + "0" * 64, "crc32:abc", "md5")
    verdicts = (None, True, False)
    states = (None, "downloaded", "skipped", "failed")
    return {verify.reason(c, v, s) for c in checksums for v in verdicts for s in states}


def test_every_verified_reason_is_documented():
    """`verified_reason` is machine-read; a value the docs omit is a trap.

    ``matched-<algo>`` is written generically in the docs because the algorithm
    comes from the repository, so it is compared as a family.
    """
    manifest_doc = (ROOT / "docs" / "manifest.md").read_text("utf-8")
    report_doc = (ROOT / "docs" / "json-report.md").read_text("utf-8")
    missing = set()
    for reason in _reasons_the_code_can_emit():
        needle = "matched-<algo>" if reason.startswith("matched-") else reason
        if needle not in manifest_doc:
            missing.add(f"manifest.md: {reason}")
        if needle not in report_doc:
            missing.add(f"json-report.md: {reason}")
    assert not missing, f"verified_reason values with no line in the docs: {sorted(missing)}"


def test_every_state_is_documented():
    """The six states a manifest row can carry, and what `verify` does with each."""
    manifest_doc = (ROOT / "docs" / "manifest.md").read_text("utf-8")
    states = {
        "downloaded",
        "skipped",
        "failed",
        "missing",
        "not-selected",
        "planned",
    }
    # A new skip state has to be documented: `verify` treats it as not-a-failure,
    # which is exactly the promise a reader needs to find in writing.
    assert states >= manifest.SKIPPED_STATES
    missing = sorted(s for s in states if f"`{s}`" not in manifest_doc)
    assert not missing, f"manifest states with no row in the table: {missing}"


def test_the_exit_codes_in_the_readme_are_the_ones_we_return():
    readme = (ROOT / "README.md").read_text("utf-8")
    for code in (
        cli.EXIT_OK,
        cli.EXIT_FAIL,
        cli.EXIT_PARTIAL,
        cli.EXIT_USAGE,
        cli.EXIT_INTERRUPT,
    ):
        assert f"`{code}`" in readme, f"exit code {code} does not appear in the README"
    assert cli.EXIT_PARTIAL == 2, "exit 2 must stay 'partial', not argparse's usage error"


def test_the_version_in_a_documented_command_matches_the_package():
    """`0.6.x` in a runbook goes stale silently; this is the one place it is checked."""
    security = (ROOT / "SECURITY.md").read_text("utf-8")
    major_minor = ".".join(consts.__version__.split(".")[:2])
    assert f"{major_minor}.x" in security, (
        f"SECURITY.md does not list the supported line {major_minor}.x"
    )


def test_the_coverage_floor_is_the_same_everywhere():
    """The gate the docs tell a contributor to run must be the gate CI runs."""
    floor = re.compile(r"--cov-fail-under[= ](\d+)")
    sources = {
        "gates.yml": ROOT / ".github" / "workflows" / "gates.yml",
        "CONTRIBUTING.md": ROOT / "CONTRIBUTING.md",
        "docs/releasing.md": ROOT / "docs" / "releasing.md",
    }
    seen = {name: floor.findall(path.read_text("utf-8")) for name, path in sources.items()}
    assert all(seen.values()), f"a documented gate lost its coverage floor: {seen}"
    values = {value for found in seen.values() for value in found}
    assert len(values) == 1, f"the coverage floor disagrees between files: {seen}"


def test_the_docs_index_lists_every_document():
    """An index nobody updates is worse than none: it reads as the whole set."""
    index = (ROOT / "docs" / "README.md").read_text("utf-8")
    present = {path.name for path in (ROOT / "docs").glob("*.md")} - {"README.md"}
    missing = sorted(name for name in present if f"]({name})" not in index)
    assert not missing, f"documents missing from docs/README.md: {missing}"

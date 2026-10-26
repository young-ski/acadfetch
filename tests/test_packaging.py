"""The packaging promises, as tests instead of prose.

Zero runtime dependencies is the project's first invariant. It is easy to break
by accident -- one helper library in one adapter -- and nothing else notices,
because pytest and ruff share the developer's environment.

``pyproject.toml`` is read with a regex rather than ``tomllib`` so this keeps
running on 3.10, where ``tomllib`` does not exist yet.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import acadfetch
from acadfetch import consts

ROOT = Path(acadfetch.__file__).parent
PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"
TEXT = PYPROJECT.read_text("utf-8")


def _project_section() -> str:
    """The [project] table, up to the next table header."""
    _, _, rest = TEXT.partition("[project]\n")
    return rest.split("\n[", 1)[0]


def _imported_modules() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for path in sorted(ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text("utf-8"), str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:  # relative import, in-package by definition
                    continue
                names = [node.module or ""]
            else:
                continue
            for name in names:
                top = name.split(".")[0]
                if top:
                    found.setdefault(str(path.relative_to(ROOT)), set()).add(top)
    return found


def test_no_third_party_imports():
    offending = {
        path: sorted(modules - sys.stdlib_module_names - {"acadfetch"})
        for path, modules in _imported_modules().items()
    }
    offending = {path: mods for path, mods in offending.items() if mods}
    assert not offending, f"third-party imports found (add a dependency argument?): {offending}"


def test_pyproject_declares_no_dependencies():
    section = _project_section()
    hit = re.search(r"^dependencies\s*=\s*(\[[^\]]*\])", section, re.M)
    assert hit is None or hit.group(1).strip() == "[]", hit.group(0) if hit else ""


def test_version_is_single_sourced():
    """`consts.__version__` is what users and the manifest see; keep it honest."""
    hit = re.search(r"^version\s*=\s*\"([^\"]+)\"", _project_section(), re.M)
    assert hit, "no version in [project]"
    assert hit.group(1) == consts.__version__


def test_requires_python_matches_the_supported_floor():
    hit = re.search(r"^requires-python\s*=\s*\"([^\"]+)\"", _project_section(), re.M)
    assert hit, "requires-python missing"
    assert hit.group(1).startswith(">=3.10")

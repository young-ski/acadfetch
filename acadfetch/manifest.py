"""manifest.json: the reproducibility artifact.

The point of a manifest is that someone else (or you, in two years) can prove
the bytes on disk are the bytes that were published, and can tell which
license they are allowed to redistribute under.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from . import atomic, consts, naming, urls, verify
from .auth import Token
from .errors import ManifestError
from .sources.base import Record, Result

SCHEMA = 3
#: Schema 1 and 2 are still read: a manifest is a document someone can hand you,
#: and the fix that made a new schema necessary must not orphan the ones already
#: written. 2 added the name on disk and ``verified_reason``; 3 adds ``selected``
#: and the ``not-selected`` / ``planned`` states, plus the provenance fields.
READABLE = (1, 2, SCHEMA)
#: States ``verify`` does not expect bytes for. A ``--only`` run never asked for
#: the other files; a ``manifest`` run never asked for any.
SKIPPED_STATES = frozenset({"not-selected", "planned"})
SCHEMA_1_NOTE = (
    "note: schema 1 records the repository's name, not the name on disk; "
    "a duplicate name can report MISMATCH on correct bytes"
)
_WINDOWS_ABS = re.compile(r"^[A-Za-z]:[\\/]")


def build(
    record: Record,
    results: list[Result],
    token: Token | None = None,
    *,
    selected_ids: set[int] | None = None,
    planned: bool = False,
    redact: bool = True,
) -> dict:
    """One row per file of the record, selected or not.

    The manifest describes the *record*, not the run: a ``--only`` fetch still
    writes a row for every file, so a later ``verify`` can tell "not fetched"
    from "fetched and then lost". ``selected_ids`` is the set of ``id(file)``
    the run asked for; ``None`` means "all of them". ``planned`` is the
    metadata-only ``manifest`` command, where nothing has been fetched yet.
    ``redact`` strips credential query parameters; ``--record-full-url`` turns
    it off.
    """
    by_file = {id(r.file): r for r in results}
    rows: list[dict] = []
    narrowed = False
    leaked = False
    for f in record.files:
        result = by_file.get(id(f))
        if result is not None:
            rows.append(_entry(f, result, True, redact=redact))
            continue
        chosen = planned or selected_ids is None or id(f) in selected_ids
        if chosen:
            rows.append(_entry(f, None, True, "planned" if planned else None, redact=redact))
        else:
            narrowed = True
            rows.append(_entry(f, None, False, redact=redact))
    target, landing = record.target, record.landing
    if redact:
        target, landing = urls.redact(target), urls.redact(landing)
        leaked = target != record.target or landing != record.landing
        if not leaked:
            leaked = any(urls.redact(f.url) != f.url for f in record.files)
    return {
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tool": {"name": "acadfetch", "version": consts.__version__},
        "source": record.source,
        "target": target,
        "doi": record.doi,
        "title": record.title,
        "license": record.license,
        "publisher": record.publisher,
        "version": record.version,
        "landing": landing,
        # True only when a run deliberately left part of the record alone.
        "selected_only": narrowed,
        # True when a credential query parameter was stripped from a URL above.
        "url_redacted": leaked,
        # Filled in by a later remote re-check; null until then.
        "remote_checked_at": None,
        # Provenance only: the variable name and a tail, never the secret.
        "auth": {"env": token.env, "hint": token.hint} if token else None,
        "files": rows,
    }


def _entry(  # noqa: ANN001
    f, result: Result | None, selected: bool, state: str | None = None, *, redact: bool = True
) -> dict:
    """One manifest row. ``name`` is what landed on disk, not what was asked for."""
    state = state or ("not-selected" if not selected else (result.state if result else "missing"))
    hide = urls.redact if redact else (lambda value: value)
    entry = {
        "name": (result.name if result and result.name else naming.sanitize(f.name)),
        "url": hide(f.url),
        "size": f.size,
        "checksum": f.checksum,
        "checksum_source": f.checksum_source,
        "sha256": _sha256(result),
        "bytes": result.bytes if result else 0,
        "state": state,
        "selected": selected,
        "verified": result.verified if result else None,
        "final_url": hide(result.final_url) if result and result.final_url else None,
        "etag": result.etag if result else None,
        "http_status": result.http_status if result else None,
        "retrieved_at": result.retrieved_at if result else None,
        # PR-2 adds final_url / etag / http_status / retrieved_at here.
        "verified_reason": (
            "transfer-failed"
            if result and result.state == "failed"
            else verify.reason(f.checksum, result.verified if result else None)
        ),
    }
    if state == "not-selected":
        entry.update(sha256=None, verified_reason="not-selected", bytes=0)
    elif state == "planned":
        entry.update(sha256=None, verified_reason="planned", bytes=0)
    return entry


def _sha256(result: Result | None) -> str | None:
    if result is None or not result.path:
        return None
    path = Path(result.path)
    if not path.is_file():
        return None
    return verify.hash_file(path, "sha256")


def write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", "utf-8")
    try:
        atomic.replace(tmp, path)
    except OSError:
        atomic.unlink(tmp)  # no stray .tmp behind
        raise


def load(path: Path) -> dict:
    """Read and shape-check a manifest, raising :class:`ManifestError`."""
    # Windows raises PermissionError for a directory, POSIX IsADirectoryError.
    if path.is_dir():
        raise ManifestError(f"{path} is a directory, not a manifest")
    try:
        raw = atomic.read_text(path)
    except FileNotFoundError as exc:
        raise ManifestError(f"no such manifest: {path}") from exc
    except UnicodeDecodeError as exc:
        raise ManifestError(f"{path} is not UTF-8: {exc}") from exc
    except OSError as exc:
        raise ManifestError(f"cannot read {path}: {exc.strerror or exc}") from exc

    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise ManifestError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ManifestError(f"{path} is not a manifest object")
    if data.get("schema") not in READABLE:
        raise ManifestError(f"unsupported manifest schema in {path}: {data.get('schema')!r}")
    if not isinstance(data.get("files"), list):
        raise ManifestError(f"{path} has no files list")
    return data


def _absolute(name: str) -> bool:
    # Path.is_absolute() is platform-bound: a Windows drive path is not absolute
    # on POSIX, where it would otherwise resolve to a directory literally named
    # "C:".
    return Path(name).is_absolute() or bool(_WINDOWS_ABS.match(name))


def _leaves_root(root: Path, name: str) -> bool:
    if _absolute(name):
        return True
    try:
        (root / name).resolve().relative_to(root.resolve())
    except ValueError:
        return True
    return False


def peek_schema(path: Path) -> int | None:
    """The schema of a manifest, without shaping it; ``None`` if unreadable."""
    try:
        data = json.loads(atomic.read_text(path))
    except (OSError, ValueError):
        return None
    value = data.get("schema") if isinstance(data, dict) else None
    return value if isinstance(value, int) else None


def verify_manifest(path: Path, root: Path, allow_escape: bool = False) -> list[dict]:
    """Re-hash every recorded file. Returns one row per file.

    A name is only allowed to point inside ``root`` unless ``allow_escape`` is
    set: a manifest is a document someone can hand you, and hashing a file
    outside the directory while reporting ``ok`` is how a green result stops
    meaning anything.
    """
    data = load(path)
    rows = []
    for entry in data.get("files") or []:
        name = entry.get("name") if isinstance(entry, dict) else None
        if not isinstance(name, str) or not name:
            rows.append({"name": "(unnamed entry)", "status": "malformed"})
            continue
        state = entry.get("state")
        if state in SKIPPED_STATES:
            # Nothing was ever asked of this file: "missing" would be a lie and
            # a bad exit code would blame the user for a --only they chose.
            rows.append({"name": name, "status": "skipped", "state": state})
            continue
        if not allow_escape and _leaves_root(root, name):
            rows.append({"name": name, "status": "escaped"})
            continue
        target = root / name
        if not target.is_file():
            rows.append({"name": name, "status": "missing"})
            continue
        actual = verify.hash_file(target, "sha256")
        status = "ok" if actual == entry.get("sha256") else "MISMATCH"
        rows.append({"name": name, "status": status, "sha256": actual})
    return rows

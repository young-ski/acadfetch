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

from . import atomic, consts, naming, verify
from .auth import Token
from .errors import ManifestError
from .sources.base import Record, Result

SCHEMA = 1
_WINDOWS_ABS = re.compile(r"^[A-Za-z]:[\\/]")


def build(record: Record, results: list[Result], token: Token | None = None) -> dict:
    by_url = {r.file.url: r for r in results}
    return {
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tool": {"name": "acadfetch", "version": consts.__version__},
        "source": record.source,
        "target": record.target,
        "doi": record.doi,
        "title": record.title,
        "license": record.license,
        "publisher": record.publisher,
        "version": record.version,
        "landing": record.landing,
        # Provenance only: the variable name and a tail, never the secret.
        "auth": {"env": token.env, "hint": token.hint} if token else None,
        "files": [
            {
                "name": naming.sanitize(f.name),
                "url": f.url,
                "size": f.size,
                "checksum": f.checksum,
                "sha256": _sha256(by_url.get(f.url)),
                "bytes": by_url[f.url].bytes if f.url in by_url else 0,
                "state": by_url[f.url].state if f.url in by_url else "missing",
                "verified": by_url[f.url].verified if f.url in by_url else None,
            }
            for f in record.files
        ],
    }


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
    if data.get("schema") != SCHEMA:
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

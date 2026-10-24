"""manifest.json: the reproducibility artifact.

The point of a manifest is that someone else (or you, in two years) can prove
the bytes on disk are the bytes that were published, and can tell which
license they are allowed to redistribute under.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from . import consts, naming, verify
from .sources.base import Record, Result

SCHEMA = 1


def build(record: Record, results: list[Result]) -> dict:
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
    os.replace(tmp, path)


def load(path: Path) -> dict:
    data = json.loads(path.read_text("utf-8"))
    if data.get("schema") != SCHEMA:
        raise ValueError(f"unsupported manifest schema: {data.get('schema')!r}")
    return data


def verify_manifest(path: Path, root: Path) -> list[dict]:
    """Re-hash every recorded file. Returns one row per file."""
    data = load(path)
    rows = []
    for entry in data.get("files") or []:
        target = root / entry["name"]
        if not target.is_file():
            rows.append({"name": entry["name"], "status": "missing"})
            continue
        actual = verify.hash_file(target, "sha256")
        status = "ok" if actual == entry.get("sha256") else "MISMATCH"
        rows.append({"name": entry["name"], "status": status, "sha256": actual})
    return rows

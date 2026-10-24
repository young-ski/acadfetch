"""Turn arbitrary repository file names into safe local paths.

Repository metadata is untrusted: it can contain path separators, NULs,
Windows reserved device names, 300-character names, and duplicates that
would silently overwrite each other.
"""

from __future__ import annotations

import os
import re
import unicodedata
from urllib.parse import unquote, urlsplit

MAX_STEM = 120
MAX_SUFFIX = 20
MAX_FULL = 180

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
_WS = re.compile(r"[ \t\n\r\f\v]+")

# CON, NUL, COM1..9, LPT1..9 are unusable as file names on Windows even with
# an extension, in every directory.
_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_RESERVED_WITH_EXT = {
    f"{base}.{ext}" for base in _RESERVED for ext in ("txt", "tar", "gz", "zip", "csv")
}


_COMPRESS = {".gz", ".bz2", ".xz", ".zst", ".zip", ".7z", ".rar"}
_EXT = re.compile(r"\.[A-Za-z0-9][A-Za-z0-9._-]*$")


def _split_ext(name: str) -> tuple[str, str]:
    """Split one extension, or a two-part one for ``archive.tar.gz`` style names."""
    stem, ext = os.path.splitext(name)
    if not ext or len(ext) > MAX_SUFFIX or not _EXT.fullmatch(ext):
        return name, ""
    if ext.lower() in _COMPRESS:
        inner_stem, inner_ext = os.path.splitext(stem)
        if inner_stem and _EXT.fullmatch(inner_ext) and len(inner_ext) <= MAX_SUFFIX:
            return inner_stem, inner_ext + ext
    return stem, ext


def sanitize(name: str, fallback: str = "file") -> str:
    """Reduce ``name`` to a single safe path component."""
    raw = unquote(name or "")
    raw = urlsplit(raw).path if ("://" in raw or raw.count("/") > 1) else raw
    raw = raw.replace("\\", "/").rsplit("/", 1)[-1]
    raw = unicodedata.normalize("NFC", raw)
    raw = _WS.sub(" ", raw)  # tabs/newlines become spaces, not underscores
    raw = _ILLEGAL.sub("_", raw)
    raw = _WS.sub(" ", raw).strip()
    raw = raw.strip(". ")

    stem, ext = _split_ext(raw)
    stem = stem.strip(". ") or fallback
    if stem.upper() in _RESERVED or f"{stem}{ext}".upper() in _RESERVED_WITH_EXT:
        stem = "_" + stem
    if len(stem) > MAX_STEM:
        stem = stem[:MAX_STEM].rstrip(". ")
    out = stem + ext[:MAX_SUFFIX]
    if len(out) > MAX_FULL:
        keep = MAX_FULL - len(ext[:MAX_SUFFIX])
        out = stem[:keep].rstrip(". ") + ext[:MAX_SUFFIX]
    return out or fallback


def unique(name: str, taken: set[str]) -> str:
    """Return a name not already in ``taken`` (case-insensitively)."""
    if name.lower() not in taken:
        taken.add(name.lower())
        return name
    stem, ext = os.path.splitext(name)
    for i in range(2, 10000):
        candidate = f"{stem} ({i}){ext}"
        if candidate.lower() not in taken:
            taken.add(candidate.lower())
            return candidate
    raise RuntimeError(f"could not dedupe {name!r}")

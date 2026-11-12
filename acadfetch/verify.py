"""Checksum parsing and streaming verification.

Repositories publish checksums as ``"md5:abc..."`` or ``"sha256:abc..."``;
some publish bare hex. Anything we cannot name an algorithm for is ignored
rather than guessed, because a wrong guess means a false VerifyError.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

KNOWN = {"md5": 32, "sha1": 40, "sha256": 64, "sha512": 128}
_BARE = re.compile(r"^[0-9a-fA-F]+$")


def parse(checksum: str | None) -> tuple[str, str] | None:
    """Return ``(algorithm, lowercase_hex)`` or ``None`` if unusable."""
    if not checksum:
        return None
    text = checksum.strip()
    algo, _, value = text.partition(":")
    if value:
        algo = algo.strip().lower()
        value = value.strip()
    else:
        algo, value = _guess(text), text
    if algo not in KNOWN:
        return None
    if len(value) != KNOWN[algo] or not _BARE.match(value):
        return None
    return algo, value.lower()


def _guess(candidate: str) -> str:
    for algo, size in KNOWN.items():
        if len(candidate) == size and _BARE.match(candidate):
            return algo
    return ""


def hash_file(path: Path, algo: str) -> str:
    return hashes(path, {algo})[algo]


def hashes(path: Path, algos: set[str]) -> dict[str, str]:
    """Every requested digest in one pass over the file."""
    digests = {algo: hashlib.new(algo) for algo in algos}
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            for digest in digests.values():
                digest.update(chunk)
    return {algo: digest.hexdigest() for algo, digest in digests.items()}


def matches(path: Path, checksum: str | None) -> bool | None:
    """True/False on a usable checksum, ``None`` when there is nothing to check."""
    parsed = parse(checksum)
    if parsed is None:
        return None
    algo, expected = parsed
    return hash_file(path, algo) == expected


def describe(checksum: str | None) -> str:
    parsed = parse(checksum)
    if parsed is None:
        return "no published checksum"
    return f"{parsed[0]}:{parsed[1][:12]}…"


def reason(checksum: str | None, verdict: bool | None, state: str | None = None) -> str:
    """A stable, machine-readable note beside ``verified``.

    ``verified: null`` on its own cannot tell "the repository published nothing"
    from "it published something we could not parse", and those are different
    promises to a reader.

    ``state`` adds the one thing a checksum cannot describe: a transfer that
    never produced bytes to check. The manifest and the ``--json`` report both
    call this, so one run cannot describe the same file two ways.
    """
    if verdict is False:
        # A failed row whose bytes were proven wrong is more useful as
        # ``mismatch`` than as ``transfer-failed``: it names the cause, not only
        # the symptom.
        return "mismatch"
    if state == "failed":
        return "transfer-failed"
    if not checksum:
        return "no-checksum-published"
    parsed = parse(checksum)
    if parsed is None:
        return "unparseable-checksum"
    if verdict is True:
        return f"matched-{parsed[0]}"
    return "unchecked"

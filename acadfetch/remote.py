"""Re-check a manifest against what the source publishes today.

``verify`` alone proves the bytes match the manifest. Nobody signs the manifest,
so someone who rewrites both the file and its row passes. Asking the source
again is the only thing that closes that: the row says what was published, the
source says what is published now, and a tampered row stops agreeing.

The result is a line per file, never a rewrite of the local check: a remote
mismatch is *added* to whatever the local verification said.
"""

from __future__ import annotations

from collections.abc import Mapping

from . import verify
from .client import Client
from .errors import AcFError
from .sources import registry, removed

MATCH = "remote_match"
MISMATCH = "remote_mismatch"
NO_CHECKSUM = "remote_no_checksum"
UNAVAILABLE = "remote_unavailable"
STATUSES = (MATCH, MISMATCH, NO_CHECKSUM, UNAVAILABLE)


def published(client: Client, data: Mapping) -> tuple[dict[str, str | None], str]:
    """URL -> checksum the source publishes now, or a reason there is none.

    One record resolution per manifest: the API returns every file at once, and
    a ``direct`` target resolves to a single HEAD.
    """
    target = data.get("target") or ""
    if not isinstance(target, str) or not target:
        return {}, "the manifest records no target"
    if removed.explain(target):
        return {}, "the source was removed; re-fetch from its file URL"
    try:
        record = registry.resolve(client, target)
    except AcFError as exc:
        return {}, str(exc)
    except OSError as exc:  # name resolution, a proxy that is not there
        return {}, str(exc)
    return {file.url: file.checksum for file in record.files}, ""


def _normalise(checksum: str | None) -> tuple[str, str] | None:
    parsed = verify.parse(checksum)
    return parsed


def judge(
    published_now: Mapping[str, str | None] | None, row: Mapping, reason: str
) -> tuple[str, str]:
    """``(status, detail)`` for one manifest row."""
    if published_now is None:
        return UNAVAILABLE, reason
    url = row.get("url")
    if not isinstance(url, str) or url not in published_now:
        return UNAVAILABLE, "no longer published at the recorded URL"
    remote = published_now[url]
    local = row.get("checksum")
    if not remote:
        return NO_CHECKSUM, "the source publishes no checksum now"
    if not local:
        return NO_CHECKSUM, "this row recorded no checksum"
    here, there = _normalise(local), _normalise(remote)
    if here is None or there is None:
        return NO_CHECKSUM, "a checksum is not parseable"
    if here[0] != there[0]:
        # Comparing an md5 with a sha256 proves nothing either way. Reporting a
        # mismatch here would call a legitimate algorithm change tampering, and
        # this status is the one that makes the whole check worth running.
        return NO_CHECKSUM, f"the source publishes {there[0]} now, the row recorded {here[0]}"
    return (
        (MATCH, "") if here == there else (MISMATCH, f"published {there[0]}:{there[1][:12]}… now")
    )


def counts(rows: list[dict]) -> dict[str, int]:
    totals = dict.fromkeys(STATUSES, 0)
    for row in rows:
        status = row.get("remote")
        if status in totals:
            totals[status] += 1
    return totals

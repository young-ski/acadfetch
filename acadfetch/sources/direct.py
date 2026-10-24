"""Direct-URL adapter: last resort for anything we recognise as plain HTTP."""

from __future__ import annotations

import contextlib
import re
from urllib.parse import unquote, urlsplit

from ..client import Client, checksum_of
from ..errors import TargetError
from .base import File, Record

_DISPOSITION = re.compile(
    r"filename\*\s*=\s*(?:UTF-8|utf-8)''([^;]+)|filename\s*=\s*\"?([^\";]+)\"?", re.I
)


def match(target: str) -> str | None:
    return target if re.match(r"^https?://", target, re.I) else None


def filename_from(url: str, headers: dict | None = None) -> str:
    if headers:
        raw = headers.get("Content-Disposition") or ""
        hit = _DISPOSITION.search(raw)
        if hit:
            return unquote(hit.group(1) or hit.group(2) or "").strip()
    path = urlsplit(url).path
    return unquote(path.rsplit("/", 1)[-1]) or "download"


def resolve(client: Client, target: str) -> Record:
    if not match(target):
        raise TargetError(f"not a URL: {target}")
    headers = client.head(target)
    size = None
    with contextlib.suppress(KeyError, TypeError, ValueError):
        size = int(headers["Content-Length"])
    return Record(
        source="direct",
        target=target,
        files=[
            File(
                url=target,
                name=filename_from(target, headers),
                size=size,
                checksum=checksum_of(headers),
            )
        ],
        title=filename_from(target, headers),
        publisher=urlsplit(target).hostname or "",
        landing=target,
    )

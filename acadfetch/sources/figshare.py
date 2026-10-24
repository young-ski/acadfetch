"""Figshare adapter.

The v2 public API returns ``computed_md5`` for every file, so verification is
available. Note that the endpoint is behind a WAF that rejects default Python
User-Agents, which is one reason every request goes through our Client.
"""

from __future__ import annotations

import re

from ..client import Client
from ..errors import SourceError
from .base import File, Record

API = "https://api.figshare.com/v2/articles/{id}"

_PATTERNS = [
    re.compile(r"^figshare:(\d+)$", re.I),
    re.compile(r"^10\.6084/m9\.figshare\.(\d+)$", re.I),
    re.compile(r"^https?://figshare\.com/(?:articles|s)/[^/]*/(\d+)/?$", re.I),
]


def match(target: str) -> str | None:
    for pattern in _PATTERNS:
        hit = pattern.match(target)
        if hit:
            return hit.group(1)
    return None


def resolve(client: Client, target: str) -> Record:
    article_id = match(target)
    if article_id is None:
        raise SourceError(f"not a Figshare reference: {target}")

    payload = client.get_json(API.format(id=article_id))
    files = [
        File(
            url=item["download_url"],
            name=item.get("name") or "file",
            size=item.get("size"),
            checksum=f"md5:{item['computed_md5']}" if item.get("computed_md5") else None,
        )
        for item in payload.get("files") or []
        if item.get("download_url")
    ]
    if not files:
        raise SourceError(f"Figshare article {article_id} publishes no downloadable files")

    return Record(
        source="figshare",
        target=target,
        files=files,
        title=payload.get("title") or "",
        doi=payload.get("doi") or "",
        license=(payload.get("license") or {}).get("name") or "",
        publisher="Figshare",
        landing=payload.get("url_public_html") or "",
    )

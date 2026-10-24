"""Zenodo adapter.

The public REST API exposes ``files[].links.self`` as a redirecting download
URL and publishes ``files[].checksum`` as ``md5:<hex>``, which is what makes
verification possible here.
"""

from __future__ import annotations

import re

from ..client import Client
from ..errors import SourceError
from .base import File, Record

API = "https://zenodo.org/api/records/{id}"

_ID_PATTERNS = [
    re.compile(r"^zenodo:(\d+)$", re.I),
    re.compile(r"^10\.5281/zenodo\.(\d+)$", re.I),
    re.compile(r"^https?://zenodo\.org/(?:records|record)/(\d+)/?$", re.I),
    # doi.org resolves Zenodo DOIs to this landing form.
    re.compile(r"^https?://zenodo\.org/doi/10\.5281/zenodo\.(\d+)/?$", re.I),
]


def match(target: str) -> str | None:
    for pattern in _ID_PATTERNS:
        hit = pattern.match(target)
        if hit:
            return hit.group(1)
    return None


def _license(payload: dict) -> str:
    meta = payload.get("metadata") or {}
    lic = meta.get("license")
    if isinstance(lic, dict) and lic.get("id"):
        return str(lic["id"])
    if isinstance(lic, str):
        return lic
    for right in meta.get("rights") or []:
        if isinstance(right, dict) and right.get("id"):
            return str(right["id"])
    return ""


def resolve(client: Client, target: str) -> Record:
    record_id = match(target)
    if record_id is None:
        raise SourceError(f"not a Zenodo reference: {target}")

    payload = client.get_json(API.format(id=record_id))
    if not payload.get("is_latest", True):
        latest = (payload.get("links") or {}).get("latest")
        if latest:
            payload = client.get_json(latest)

    if (payload.get("access") or {}).get("files") == "restricted":
        raise SourceError(
            f"record {record_id} has restricted files; retry with "
            "--header 'Authorization: Bearer <your zenodo token>'"
        )

    meta = payload.get("metadata") or {}
    files: list[File] = []
    for item in payload.get("files") or []:
        link = (item.get("links") or {}).get("self")
        if not link:
            continue
        files.append(
            File(
                url=link,
                name=item.get("key") or item.get("id") or "file",
                size=item.get("size"),
                checksum=item.get("checksum"),
            )
        )
    if not files:
        raise SourceError(f"record {record_id} publishes no downloadable files")

    return Record(
        source="zenodo",
        target=target,
        files=files,
        title=str(meta.get("title") or ""),
        doi=str(payload.get("doi") or meta.get("doi") or ""),
        license=_license(payload),
        publisher=str(meta.get("publisher") or "Zenodo"),
        version=str(meta.get("version") or ""),
        landing=str((payload.get("links") or {}).get("self_html") or ""),
    )

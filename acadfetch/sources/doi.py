"""DOI resolver.

A DOI is the canonical way to cite data, so it is the input users actually
have. We resolve the handle to a URL and then hand off to whichever adapter
owns that registration agency's host.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from urllib.parse import quote, urlsplit

from ..client import Client
from ..errors import HttpError, SourceError, TargetError

HANDLE = "https://doi.org/api/handles/{doi}"
_DOI = re.compile(r"^10\.\d{4,9}/\S+$")


def match(target: str) -> str | None:
    return target if _DOI.match(target) else None


LANDING_HINT = """DOI {doi} resolves to a web page, not a data file:
  {url}
  Open it, find the file, then fetch that URL directly (add --checksum <algo>:<hex> if the
  page publishes one)."""


def _page_like(client: Client, url: str, content_type: str) -> bool:
    """Whether a resolved URL is a front door rather than a file.

    Content-Type decides when the server sends one; Mendeley sends none, so the
    fallback asks for 512 bytes and looks at them. An extension-less URL that
    declares no length and starts with ``<`` is markup.
    """
    if "html" in content_type.lower():
        return True
    if content_type:
        return False
    if PurePosixPath(urlsplit(url).path).suffix:
        return False
    try:
        with client.open(url, {"Range": "bytes=0-511"}, attempts=1) as resp:
            ctype = resp.headers.get("Content-Type") or ""
            head = resp.read(512)
    except (OSError, ValueError, HttpError):
        return False
    if "html" in ctype.lower():
        return True
    return head.lstrip()[:1] == b"<"


def resolve(client: Client, target: str) -> object:
    url = resolve_url(client, target)
    from . import registry  # late import: registry imports this module

    record = registry.resolve(client, url, allow_doi=False)
    if record.source == "direct" and _page_like(client, url, record.content_type):
        raise TargetError(LANDING_HINT.format(doi=target, url=url))
    if not record.doi:
        # Keep the DOI: it is the unique name for the output directory and the
        # only citation the caller had.
        record.doi = target
    return record


def resolve_url(client: Client, doi: str) -> str:
    try:
        payload = client.get_json(HANDLE.format(doi=quote(doi, safe="/")))
    except Exception as exc:  # noqa: BLE001
        raise TargetError(f"could not resolve DOI {doi}: {exc}") from exc

    if payload.get("responseCode") != 1:
        raise TargetError(
            f"DOI {doi} is not registered (responseCode {payload.get('responseCode')})"
        )

    for value in payload.get("values") or []:
        if value.get("type") == "URL":
            data = (value.get("data") or {}).get("value")
            if data:
                return data
    raise SourceError(f"DOI {doi} has no URL value")

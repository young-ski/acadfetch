"""DOI resolver.

A DOI is the canonical way to cite data, so it is the input users actually
have. We resolve the handle to a URL and then hand off to whichever adapter
owns that registration agency's host.
"""

from __future__ import annotations

import re
from urllib.parse import quote

from ..client import Client
from ..errors import SourceError, TargetError

HANDLE = "https://doi.org/api/handles/{doi}"
_DOI = re.compile(r"^10\.\d{4,9}/\S+$")

# Registration agencies whose landing URLs map onto a real adapter.
AGENCY_PREFIXES = (
    ("10.5281/", "zenodo"),
    ("10.6084/", "figshare"),
    ("10.48550/", "arxiv"),
)


def match(target: str) -> str | None:
    return target if _DOI.match(target) else None


def resolve(client: Client, target: str) -> object:
    url = resolve_url(client, target)
    from . import registry  # late import: registry imports this module

    return registry.resolve(client, url, allow_doi=False)


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

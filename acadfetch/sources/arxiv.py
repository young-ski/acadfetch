"""arXiv adapter.

Requests go over https and the API response is closed; arXiv happens to
send ``Connection: close`` today, which is what hid a missing ``with`` here.

arXiv serves PDFs through a redirecting URL and publishes no checksum, so
files here are size-unknown and verified only by byte count.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from ..client import Client
from ..errors import SourceError
from .base import File, Record

API = "https://export.arxiv.org/api/query?id_list={id}"

_PATTERNS = [
    re.compile(r"^arxiv:(?P<id>[\w.\-/]+)$", re.I),
    re.compile(r"^(?P<id>\d{4}\.\d{4,5}(?:v\d+)?)$"),
    re.compile(r"^https?://arxiv\.org/(?:abs|pdf)/(?P<id>[\w.\-/]+?)(?:\.pdf)?$", re.I),
]
_NS = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


def match(target: str) -> str | None:
    for pattern in _PATTERNS:
        hit = pattern.match(target)
        if hit:
            return hit.group("id").rstrip("/")
    return None


def resolve(client: Client, target: str) -> Record:
    arxiv_id = match(target)
    if arxiv_id is None:
        raise SourceError(f"not an arXiv reference: {target}")

    with client.open(API.format(id=arxiv_id)) as resp:
        xml = resp.read()
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise SourceError(f"unparseable arXiv response: {exc}") from exc

    entry = root.find("a:entry", _NS)
    if entry is None:
        raise SourceError(f"arXiv id {arxiv_id} not found")

    title = " ".join((entry.findtext("a:title", default="", namespaces=_NS)).split())
    abs_url = entry.findtext("a:id", default="", namespaces=_NS)
    if "api/errors" in abs_url:
        raise SourceError(f"arXiv id {arxiv_id} not found")

    base_id = arxiv_id.split("v")[0]
    return Record(
        source="arxiv",
        target=target,
        files=[File(url=f"https://arxiv.org/pdf/{arxiv_id}", name=f"{base_id}.pdf")],
        title=title,
        doi=f"10.48550/arXiv.{base_id}",
        license="arxiv-nonexclusive-distrib",
        publisher="arXiv",
        version=arxiv_id[len(base_id) :].lstrip("v"),
        landing=abs_url.replace("http://", "https://") if abs_url else "",
    )

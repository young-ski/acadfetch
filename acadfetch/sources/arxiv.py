"""arXiv adapter.

Requests go over https and the API response is closed; arXiv happens to
send ``Connection: close`` today, which is what hid a missing ``with`` here.

arXiv serves PDFs through a redirecting URL and publishes no checksum, so
files here are size-unknown and verified only by byte count.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from .. import naming
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
#: Legacy ids carry a slash and can contain a ``v`` (``solv-int/9701001v2``), so
#: a version is only the trailing ``v<digits>``, never "everything after the
#: first v".
_VERSION = re.compile(r"^(?P<base>.+?)(?:v(?P<version>\d+))?$")


def _split_version(arxiv_id: str) -> tuple[str, str]:
    hit = _VERSION.match(arxiv_id)
    if hit is None:  # pragma: no cover - the pattern always matches
        return arxiv_id, ""
    return hit.group("base"), hit.group("version") or ""


def _license(entry: ET.Element) -> str:
    """The submitter's licence, as arXiv reports it.

    ``arxiv:license`` carries the licence URL; the API omits it for older
    submissions, and a hardcoded default would then claim a licence nobody
    chose. The short id (``cc_by``, ``nonexclusive-distrib``) is enough for a
    manifest a human reads next to ``landing``.
    """
    node = entry.find("arxiv:license", _NS)
    if node is None:
        return "unknown"
    href = (node.get("href") or node.text or "").strip()
    parts = [part for part in href.split("/") if part]
    while parts and re.fullmatch(r"\d+(\.\d+)*", parts[-1]):
        parts.pop()  # drop the version segment: .../cc_by/4.0
    return parts[-1] if parts else "unknown"


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

    base_id, version = _split_version(arxiv_id)
    return Record(
        source="arxiv",
        target=target,
        files=[
            File(
                url=f"https://arxiv.org/pdf/{arxiv_id}",
                name=naming.sanitize(f"{base_id.replace('/', '_')}.pdf"),
            )
        ],
        title=title,
        doi=f"10.48550/arXiv.{base_id}",
        license=_license(entry),
        publisher="arXiv",
        version=version,
        landing=abs_url.replace("http://", "https://") if abs_url else "",
    )

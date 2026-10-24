"""Source registry: one place that decides which adapter owns a target."""

from __future__ import annotations

import re

from ..client import Client
from ..errors import TargetError
from . import arxiv, direct, doi, figshare, zenodo
from .base import Record

# Ordered: more specific patterns first. ``direct`` accepts any http(s) URL,
# so it must stay behind the repository adapters.
ADAPTERS = (zenodo, figshare, arxiv, direct)

# doi.org hands out landing URLs; unwrap them before dispatch.
_DOI_URL = re.compile(r"^https?://(?:dx\.)?doi\.org/(10\.\S+)$", re.I)


def names() -> list[str]:
    return ["zenodo", "figshare", "arxiv", "doi", "direct"]


def resolve(client: Client, target: str, allow_doi: bool = True) -> Record:
    text = " ".join((target or "").split())
    if not text:
        raise TargetError("empty target")

    hit = _DOI_URL.match(text)
    if hit:
        text = hit.group(1)

    if allow_doi and doi.match(text):
        record = doi.resolve(client, text)
        record.target = text  # keep what the user typed in the manifest
        return record

    for adapter in ADAPTERS:
        if adapter.match(text):
            return adapter.resolve(client, text)

    # A bare integer is a Zenodo record id far more often than anything else.
    if text.isdigit():
        return zenodo.resolve(client, f"zenodo:{text}")

    raise TargetError(
        f"cannot tell what {text!r} is; try a DOI, a zenodo/figshare/arxiv id, or a URL.\n"
        f"known sources: {', '.join(names())}"
    )

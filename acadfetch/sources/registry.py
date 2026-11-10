"""Source registry: one place that decides which adapter owns a target."""

from __future__ import annotations

import re

from ..client import Client
from ..errors import TargetError
from . import arxiv, direct, doi, removed, zenodo
from .base import Record

# Ordered: more specific patterns first. ``direct`` accepts any http(s) URL,
# so it must stay behind the repository adapters.
ADAPTERS = (zenodo, arxiv, direct)

# doi.org hands out landing URLs; unwrap them before dispatch.
_DOI_URL = re.compile(r"^https?://(?:dx\.)?doi\.org/(10\.\S+)$", re.I)


def names() -> list[str]:
    return ["zenodo", "arxiv", "doi", "direct"]


def _name_of(adapter) -> str:  # noqa: ANN001
    return adapter.__name__.rsplit(".", 1)[-1]


def _dispatch(adapter, client: Client, text: str) -> Record:  # noqa: ANN001
    """Route through ``adapter`` with the client tagged for its credentials."""
    client.source = _name_of(adapter)
    return adapter.resolve(client, text)


def resolve(client: Client, target: str, allow_doi: bool = True) -> Record:
    text = " ".join((target or "").split())
    if not text:
        raise TargetError("empty target")

    hit = _DOI_URL.match(text)
    if hit:
        text = hit.group(1)

    # Before the DOI resolver: 10.6084/… is a DOI, and figshare's landing page
    # would otherwise be handed to ``direct`` and downloaded as data.
    if hint := removed.explain(text):
        raise TargetError(hint)

    if allow_doi and doi.match(text):
        client.source = "doi"
        record = doi.resolve(client, text)
        record.target = text  # keep what the user typed in the manifest
        return record

    for adapter in ADAPTERS:
        if adapter.match(text):
            return _dispatch(adapter, client, text)

    # A bare integer is a Zenodo record id far more often than anything else.
    if text.isdigit():
        return _dispatch(zenodo, client, f"zenodo:{text}")

    raise TargetError(
        f"cannot tell what {text!r} is; try a DOI, a zenodo/arxiv id, or a URL "
        f"(known sources: {', '.join(names())})"
    )

"""Targets whose adapter was removed, and what to do instead.

A removed source is not an unknown one: ``figshare:1`` used to work, so "cannot
tell what 'figshare:1' is" is a worse answer than naming the replacement. These
checks run before the DOI resolver, because ``10.6084/…`` is a DOI and would
otherwise land on figshare's HTML landing page and be downloaded as if it were
data.
"""

from __future__ import annotations

import re

FIGSHARE = """figshare support was removed in 0.2.0.
  Open https://figshare.com/articles/{id} in a browser, copy the download link
  (it looks like https://ndownloader.figshare.com/files/<file_id>/<name>), then:
      acadfetch fetch https://ndownloader.figshare.com/files/<file_id>/<name> \\
          --checksum md5:<computed_md5>
  An existing figshare manifest still verifies: `acadfetch verify` needs no adapter."""

_PATTERNS = (
    (re.compile(r"^figshare:(?P<id>\d+)$", re.I), FIGSHARE),
    (re.compile(r"^10\.6084/\S*?figshare\.(?P<id>\d+)", re.I), FIGSHARE),
    (
        re.compile(r"^https?://(?:www\.)?figshare\.com/(?:articles|s)/[^/]*/?(?P<id>\d+)?", re.I),
        FIGSHARE,
    ),
)

#: What ``sources`` prints under the live adapters.
SOURCES_NOTE = "figshare  removed in 0.2.0 - pass the ndownloader URL"


def explain(target: str) -> str | None:
    """Migration text when ``target`` belongs to a removed source."""
    for pattern, text in _PATTERNS:
        hit = pattern.match(target)
        if hit:
            record_id = hit.groupdict().get("id") or "<id>"
            # str.replace, not format: the text also shows ``{computed_md5}``.
            return text.replace("{id}", record_id)
    return None

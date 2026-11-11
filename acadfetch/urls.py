"""Keep credentials out of URLs before they are written down.

A presigned URL is a bearer credential in query form: whoever reads
``manifest.json``, a CI log or the terminal can fetch the object while the
signature lives. The manifest is supposed to be committed next to an analysis,
so it is the worst place to leave one.

Redaction is lossy on purpose. ``--record-full-url`` keeps the original string
when someone needs to replay it exactly.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

#: Query keys whose value is a credential. Compared lowercase.
SECRET_PARAMS = frozenset(
    {
        "x-amz-signature",
        "x-amz-credential",
        "x-amz-security-token",
        "signature",
        "sig",
        "token",
        "access_token",
    }
)

#: A URL inside a sentence; the last character excludes the punctuation that
#: often follows one (`: <urlopen …>`, `,`, `)`).
_URL = re.compile(r"https?://[^\s'\"<>]*[^\s'\"<>:.,;)]")


def redact(url: str) -> str:
    """Drop credential query parameters; return the URL unchanged otherwise."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if not parts.query:
        return url
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    kept = [(key, value) for key, value in pairs if key.lower() not in SECRET_PARAMS]
    if len(kept) == len(pairs):
        return url
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(kept), parts.fragment))


def redact_text(text: str) -> str:
    """Redact every URL embedded in a sentence (error messages, log lines)."""
    return _URL.sub(lambda hit: redact(hit.group(0)), text)

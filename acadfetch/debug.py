"""Opt-in tracing, so a bug report does not have to be a guess.

Everything here writes to stderr and swallows its own errors: like progress, a
log line must never be able to fail a download. Header values that are
credentials are replaced with an ellipsis, and URLs arrive already redacted by
the caller.
"""

from __future__ import annotations

import contextlib
import os
import sys
import traceback
from collections.abc import Mapping

_ON = False
_CREDENTIAL_HEADERS = frozenset(
    {"authorization", "cookie", "proxy-authorization", "x-api-key", "x-auth-token"}
)
_FALSE = frozenset({"", "0", "false", "no", "off"})


def configure(flag: bool = False, environ: Mapping[str, str] | None = None) -> None:
    """Turn tracing on from the flag or ``ACADFETCH_DEBUG``; the flag wins."""
    global _ON
    env = os.environ if environ is None else environ
    value = (env.get("ACADFETCH_DEBUG") or "").strip().lower()
    _ON = bool(flag) or value not in _FALSE


def enabled() -> bool:
    return _ON


def log(message: str) -> None:
    if not _ON:
        return
    with contextlib.suppress(OSError, ValueError):
        print(f"debug: {message}", file=sys.stderr, flush=True)


def log_exception(exc: BaseException) -> None:
    if not _ON:
        return
    try:
        traceback.print_exception(type(exc), exc, exc.__traceback__, file=sys.stderr)
        sys.stderr.flush()
    except (OSError, ValueError):
        pass


def headers_for_log(headers: Mapping[str, str]) -> str:
    """Header names with safe values; credentials become ``…``."""
    return " ".join(
        f"{name}: {'…' if name.lower() in _CREDENTIAL_HEADERS else value}"
        for name, value in headers.items()
    )

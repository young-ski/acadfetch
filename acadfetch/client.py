"""Single choke point for every HTTP request.

Everything outbound goes through :class:`Client` so that rate limiting, the
User-Agent, retry policy, and ``Retry-After`` handling cannot drift between
adapters. Only idempotent methods are retried.
"""

from __future__ import annotations

import email.utils
import json
import random
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from datetime import datetime, timezone

from . import consts, verify
from .errors import HttpError
from .ratelimit import TokenBucket

RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}
MAX_RETRY_AFTER = 120.0
MAX_JSON = 32 << 20


def parse_retry_after(value: str | None) -> float | None:
    """Parse the two legal forms of Retry-After. Returns seconds, clamped."""
    if not value:
        return None
    text = value.strip()
    if text.isdigit():
        return min(float(text), MAX_RETRY_AFTER)
    try:
        when = email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    delta = (when - datetime.now(timezone.utc)).total_seconds()
    return max(0.0, min(delta, MAX_RETRY_AFTER))


class Client:
    def __init__(
        self,
        rate: float = consts.DEFAULT_RATE,
        retries: int = consts.DEFAULT_RETRIES,
        timeout: float = consts.DEFAULT_TIMEOUT,
        user_agent: str = consts.USER_AGENT,
        headers: Mapping[str, str] | None = None,
        auth: Mapping[str, Mapping[str, str]] | None = None,
    ) -> None:
        self.bucket = TokenBucket(rate)
        self.retries = max(0, retries)
        self.timeout = timeout
        self.user_agent = user_agent
        self.headers = dict(headers or {})
        # Per-source credentials. The registry sets ``source`` before dispatch
        # so a Zenodo token is never sent to figshare.
        self.auth = {k: dict(v) for k, v in (auth or {}).items()}
        self.source: str | None = None

    # -- internals ---------------------------------------------------------
    def _sleep(self, attempt: int, retry_after: float | None) -> None:
        if retry_after is not None:
            time.sleep(retry_after)
            return
        backoff = min(0.8 * (2**attempt), 30.0)
        time.sleep(backoff * (0.7 + random.random() * 0.6))

    def _request(
        self, url: str, headers: Mapping[str, str] | None = None
    ) -> urllib.request.Request:
        merged: dict[str, str] = {}
        if self.source and self.source in self.auth:
            merged.update(self.auth[self.source])
        merged["User-Agent"] = self.user_agent
        merged["Accept-Encoding"] = "identity"
        merged.update(self.headers)  # explicit --header wins over a token
        if headers:
            merged.update(headers)
        return urllib.request.Request(url, headers=merged)

    def _open(self, req: urllib.request.Request):  # noqa: ANN202
        return urllib.request.urlopen(req, timeout=self.timeout)

    def _with_retries(self, url: str, fn, headers=None):  # noqa: ANN001, ANN202
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            self.bucket.acquire()
            try:
                return fn(self._request(url, headers))
            except urllib.error.HTTPError as exc:
                last = exc
                if exc.code not in RETRY_STATUS:
                    detail = _snippet(exc)
                    raise HttpError(exc.code, url, detail) from exc
                retry_after = parse_retry_after(
                    exc.headers.get("Retry-After") if exc.headers else None
                )
                exc.close()
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last = exc
                retry_after = None
            if attempt < self.retries:
                self._sleep(attempt, retry_after)
        if isinstance(last, urllib.error.HTTPError):
            raise HttpError(last.code, url, _snippet(last)) from last
        raise HttpError(0, url, str(last)) from last

    # -- public ------------------------------------------------------------
    def open(self, url: str, headers: Mapping[str, str] | None = None):
        """Open a stream. Caller must close it."""
        return self._with_retries(url, self._open, headers)

    def get_json(self, url: str, headers: Mapping[str, str] | None = None) -> dict:
        with self.open(url, headers) as resp:
            raw = resp.read(MAX_JSON)
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HttpError(0, url, f"invalid JSON: {exc}") from exc

    def head(self, url: str) -> dict:
        def _head(req: urllib.request.Request):
            req.method = "HEAD"
            return self._open(req)

        try:
            with self._with_retries(url, _head) as resp:
                return dict(resp.headers)
        except HttpError as exc:
            if exc.status in (403, 405, 501):
                return {}
            raise


def _snippet(exc: urllib.error.HTTPError) -> str:
    try:
        text = exc.read(200).decode("utf-8", "replace").strip()
    except Exception:  # noqa: BLE001
        return ""
    return " ".join(text.split())[:200]


def checksum_of(headers: Mapping[str, str]) -> str | None:
    """Prefer a strong hash header over nothing (for direct URLs)."""
    for key in ("X-Checksum-Sha256", "X-Amz-Meta-Sha256", "Content-MD5"):
        if key in headers:
            algo = "sha256" if "sha256" in key.lower() else "md5"
            return f"{algo}:{headers[key]}"
    return None


__all__ = ["Client", "parse_retry_after", "checksum_of", "verify"]

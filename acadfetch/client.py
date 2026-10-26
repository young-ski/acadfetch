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
SNIPPET = 200


def is_block_page(detail: str, content_type: str | None) -> bool:
    """Tell a WAF/load-balancer 403 apart from the API's own permission 403.

    Block pages are HTML or empty (AWS ELB, Cloudflare and friends all ship
    one — see ``tests/fixtures/figshare_403_block.html``); a repository's real
    permission error is JSON or plain text from the application itself. Both
    ``api.figshare.com`` endpoints answer the HTML one intermittently, and it
    clears within a retry or two, so it must not be terminal.

    An empty body counts as a block page: retrying costs one request, while
    misreading it as terminal costs the whole download.
    """
    if content_type and "html" in content_type.lower():
        return True
    head = detail.lstrip()[:1]
    return not head or head == "<"


def _blocked_detail(detail: str, retries: int | None) -> str:
    """Block-page markup is noise in a one-line error; name it instead."""
    named = "WAF block page"
    if retries is not None:
        named += f", gave up after {retries} retr{'y' if retries == 1 else 'ies'}"
    if detail.startswith("<"):
        return named
    return f"{detail} ({named})"


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
        block_retries: int = consts.WAF_403_RETRIES,
    ) -> None:
        self.bucket = TokenBucket(rate)
        self.retries = max(0, retries)
        self.timeout = timeout
        self.user_agent = user_agent
        # How often a WAF block page is worth retrying. 0 turns the feature
        # off, which is what a run from a network the WAF likes wants.
        self.block_retries = max(0, block_retries)
        self.headers = dict(headers or {})
        # Per-source credentials. The registry sets ``source`` before dispatch
        # so a Zenodo token is never sent to figshare.
        self.auth = {k: dict(v) for k, v in (auth or {}).items()}
        self.source: str | None = None

    # -- internals ---------------------------------------------------------
    def _sleep(self, attempt: int, retry_after: float | None) -> None:
        if retry_after is not None:
            # A literal `Retry-After: 0` would mean no gap at all and a burst;
            # keep a small jittered floor under it.
            time.sleep(max(retry_after, 0.05 + random.random() * 0.15))
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

    def _with_retries(
        self,
        url: str,
        fn,
        headers=None,
        block_retries: int | None = None,
        attempts: int | None = None,
    ):  # noqa: ANN001, ANN202
        if block_retries is None:
            block_retries = self.block_retries
        budget = self.retries + 1 if attempts is None else max(1, attempts)
        last: Exception | None = None
        last_detail = ""
        last_blocked = False
        waf = 0
        for attempt in range(budget):
            self.bucket.acquire()
            retry_after = None
            try:
                return fn(self._request(url, headers))
            except urllib.error.HTTPError as exc:
                last = exc
                last_detail = detail = _snippet(exc)
                content_type = exc.headers.get("Content-Type") if exc.headers else None
                last_blocked = exc.code == 403 and is_block_page(detail, content_type)
                retry_after = parse_retry_after(
                    exc.headers.get("Retry-After") if exc.headers else None
                )
                exc.close()
                if exc.code in RETRY_STATUS:
                    pass
                elif not last_blocked:
                    raise HttpError(exc.code, url, detail) from exc
                elif waf >= block_retries:
                    raise HttpError(
                        403,
                        url,
                        _blocked_detail(last_detail, waf if budget > 1 else None),
                        transient=True,
                    ) from exc
                else:
                    waf += 1
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last = exc
                last_detail = str(exc)
                last_blocked = False
            if attempt < budget - 1:
                self._sleep(attempt, retry_after)
        if isinstance(last, urllib.error.HTTPError):
            detail = (
                _blocked_detail(last_detail, waf if budget > 1 else None)
                if last_blocked
                else last_detail
            )
            raise HttpError(last.code, url, detail, transient=True) from last
        raise HttpError(0, url, last_detail or str(last)) from last

    # -- public ------------------------------------------------------------
    def open(self, url: str, headers: Mapping[str, str] | None = None, attempts: int | None = None):
        """Open a stream. Caller must close it.

        ``attempts`` overrides the retry budget, for callers that manage their
        own (the transfer engine does, because it owns the resume state).
        """
        return self._with_retries(url, self._open, headers, attempts=attempts)

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
            # HEAD only feeds size/filename; one shot, never worth a retry.
            with self._with_retries(url, _head, block_retries=0, attempts=1) as resp:
                return dict(resp.headers)
        except HttpError as exc:
            if exc.status in (403, 405, 501):
                return {}
            raise


def _snippet(exc: urllib.error.HTTPError) -> str:
    try:
        raw = exc.read(SNIPPET)
    except Exception:  # noqa: BLE001
        return ""
    text = " ".join(raw.decode("utf-8", "replace").split())
    return text[:SNIPPET]


def checksum_of(headers: Mapping[str, str]) -> str | None:
    """Prefer a strong hash header over nothing (for direct URLs)."""
    for key in ("X-Checksum-Sha256", "X-Amz-Meta-Sha256", "Content-MD5"):
        if key in headers:
            algo = "sha256" if "sha256" in key.lower() else "md5"
            return f"{algo}:{headers[key]}"
    return None


__all__ = ["Client", "parse_retry_after", "checksum_of", "verify"]

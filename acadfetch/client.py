"""Single choke point for every HTTP request.

Everything outbound goes through :class:`Client` so that rate limiting, the
User-Agent, retry policy, and ``Retry-After`` handling cannot drift between
adapters. Only idempotent methods are retried.
"""

from __future__ import annotations

import base64
import binascii
import email.utils
import hashlib
import json
import random
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from datetime import datetime, timezone
from http.client import HTTPException

from . import consts, debug, urls, verify
from .errors import HttpError
from .ratelimit import TokenBucket

RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}
MAX_RETRY_AFTER = 120.0
MAX_JSON = 32 << 20
SNIPPET = 200


def is_block_page(detail: str, content_type: str | None) -> bool:
    """Tell a WAF/load-balancer 403 apart from the application's own 403.

    Block pages are HTML or empty (AWS ELB, Cloudflare and friends all ship
    one — see ``tests/fixtures/figshare_403_block.html``); a real permission
    error is JSON or plain text from the application itself. A block page
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


class RedirectPolicy(urllib.request.HTTPRedirectHandler):
    """Keep credentials on their own origin, and off a plain-http hop.

    urllib copies every header onto the redirect, so a token issued for Zenodo
    would travel to whatever host it points at -- and across the wire in clear
    if the hop is ``https`` -> ``http``. ``--allow-http`` keeps the header on
    such a hop for mirrors that need it.
    """

    def __init__(self, allow_http: bool = False) -> None:
        self.allow_http = allow_http
        self.downgrades: list[tuple[str, str]] = []

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is None:
            return None
        before = urllib.parse.urlsplit(req.full_url)
        after = urllib.parse.urlsplit(new.full_url)
        if (before.hostname, before.port) != (after.hostname, after.port):
            # A different origin has no business seeing the credentials.
            for header in ("Authorization", "Cookie", "Proxy-Authorization"):
                _drop_header(new, header)
        if after.scheme == "http" and before.scheme == "https":
            self.downgrades.append((req.full_url, new.full_url))
            if self.allow_http:
                note = "credentials kept (--allow-http)"
            else:
                _drop_header(new, "Authorization")
                note = "credentials dropped (use --allow-http to keep them)"
            print(
                f"acadfetch: https -> http at {after.hostname}; {note}",
                file=sys.stderr,
            )
        return new


def _drop_header(req, name: str) -> None:  # noqa: ANN001
    """Delete a header whatever case it was stored under.

    ``Request.add_header`` capitalises only the first letter, so
    ``remove_header("Proxy-Authorization")`` misses ``Proxy-authorization``.
    """
    for store in (req.headers, req.unredirected_hdrs):
        for key in [k for k in store if k.lower() == name.lower()]:
            del store[key]


def _carries_its_own_auth(url: str) -> bool:
    """A presigned URL (S3 and friends) fails if Authorization is added."""
    query = urllib.parse.urlsplit(url).query.lower()
    return "x-amz-signature" in query or "x-amz-credential" in query


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
        allow_http: bool = False,
        proxy: Mapping[str, str] | None = None,
        no_proxy: bool = False,
        ca_bundle: str | None = None,
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
        # so a Zenodo token is never sent to another source's host.
        self.auth = {k: dict(v) for k, v in (auth or {}).items()}
        self.source: str | None = None
        self.redirects = RedirectPolicy(allow_http=allow_http)
        handlers: list = [self.redirects]
        # An explicit ProxyHandler replaces urllib's environment handling; the
        # empty dict is how --no-proxy ignores HTTP(S)_PROXY.
        if proxy is not None or no_proxy:
            handlers.append(urllib.request.ProxyHandler(dict(proxy or {})))
        if ca_bundle:
            context = ssl.create_default_context(cafile=ca_bundle)
            handlers.append(urllib.request.HTTPSHandler(context=context))
        self.opener = urllib.request.build_opener(*handlers)

    # -- internals ---------------------------------------------------------
    def _sleep(self, attempt: int, retry_after: float | None) -> None:
        if retry_after is not None:
            # A literal `Retry-After: 0` would mean no gap at all and a burst;
            # keep a small jittered floor under it.
            delay = max(retry_after, 0.05 + random.random() * 0.15)
        else:
            delay = min(0.8 * (2**attempt), 30.0) * (0.7 + random.random() * 0.6)
        debug.log(f"retry in {delay:.2f}s")
        time.sleep(delay)

    def _request(
        self, url: str, headers: Mapping[str, str] | None = None
    ) -> urllib.request.Request:
        merged: dict[str, str] = {}
        if self.source and self.source in self.auth and not _carries_its_own_auth(url):
            merged.update(self.auth[self.source])
        merged["User-Agent"] = self.user_agent
        merged["Accept-Encoding"] = "identity"
        merged.update(self.headers)  # explicit --header wins over a token
        if headers:
            merged.update(headers)
        return urllib.request.Request(url, headers=merged)

    def _open(self, req: urllib.request.Request):  # noqa: ANN202
        return self.opener.open(req, timeout=self.timeout)

    def _with_retries(
        self,
        url: str,
        fn,
        headers=None,
        block_retries: int | None = None,
        attempts: int | None = None,
        method: str = "GET",
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
            req = self._request(url, headers)
            debug.log(f"{method} {urls.redact(url)} headers[{debug.headers_for_log(req.headers)}]")
            try:
                resp = fn(req)
                debug.log(f"-> {getattr(resp, 'status', 'ok')}")
                return resp
            except urllib.error.HTTPError as exc:
                last = exc
                last_detail = detail = _snippet(exc)
                debug.log(f"-> {exc.code} {detail[:120]}")
                content_type = exc.headers.get("Content-Type") if exc.headers else None
                last_blocked = exc.code == 403 and is_block_page(detail, content_type)
                retry_after = parse_retry_after(
                    exc.headers.get("Retry-After") if exc.headers else None
                )
                exc.close()
                if exc.code in RETRY_STATUS:
                    pass
                elif not last_blocked:
                    raise HttpError(
                        exc.code,
                        url,
                        detail,
                        content_range=exc.headers.get("Content-Range") if exc.headers else None,
                    ) from exc
                elif waf >= block_retries:
                    raise HttpError(
                        403,
                        url,
                        _blocked_detail(last_detail, waf if budget > 1 else None),
                        transient=True,
                    ) from exc
                else:
                    waf += 1
                    debug.log(f"-> block page, retry {waf}/{block_retries}")
            except HttpError as exc:
                # Raised by a caller-supplied fn, e.g. a body that stopped
                # short. Only the transient ones are worth another request.
                if not exc.transient:
                    raise
                last = exc
                last_detail = str(exc)
                last_blocked = False
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                debug.log(f"-> {type(exc).__name__}: {exc}")
                last = exc
                last_detail = str(exc)
                last_blocked = False
            if attempt < budget - 1:
                self._sleep(attempt, retry_after)
        if isinstance(last, HttpError):
            raise last
        if isinstance(last, urllib.error.HTTPError):
            detail = (
                _blocked_detail(last_detail, waf if budget > 1 else None)
                if last_blocked
                else last_detail
            )
            raise HttpError(
                last.code,
                url,
                detail,
                transient=True,
                content_range=last.headers.get("Content-Range") if last.headers else None,
            ) from last
        raise HttpError(0, url, last_detail or str(last)) from last

    # -- public ------------------------------------------------------------
    def open(self, url: str, headers: Mapping[str, str] | None = None, attempts: int | None = None):
        """Open a stream. Caller must close it.

        ``attempts`` overrides the retry budget, for callers that manage their
        own (the transfer engine does, because it owns the resume state).
        """
        return self._with_retries(url, self._open, headers, attempts=attempts)

    def get_json(self, url: str, headers: Mapping[str, str] | None = None) -> dict:
        def _read(req):  # noqa: ANN001
            with self._open(req) as resp:
                try:
                    return resp.read(MAX_JSON)
                except (HTTPException, TimeoutError, OSError) as exc:
                    # IncompleteRead is an HTTPException, not an OSError: left
                    # alone it escaped the retry loop as a traceback.
                    raise HttpError(
                        0, url, f"could not read the response body: {exc}", transient=True
                    ) from exc

        raw = self._with_retries(url, _read, headers)
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
            with self._with_retries(url, _head, block_retries=0, attempts=1, method="HEAD") as resp:
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


def header_value(headers: Mapping[str, str], name: str) -> str | None:
    """Case-insensitive header lookup; HTTP names are not case sensitive."""
    wanted = name.lower()
    for key, value in headers.items():
        if key.lower() == wanted:
            return value
    return None


def _as_hex(value: str | None, algo: str) -> str | None:
    """Accept a header checksum as hex or as base64, return ``algo:hex``.

    ``Content-MD5`` and ``x-amz-checksum-sha256`` are base64 by standard, while
    some CDNs put bare hex in ``X-Checksum-Sha256``. A wrong guess here means a
    false MISMATCH, so both forms are tried and anything else is ignored.
    """
    if not value:
        return None
    text = value.strip()
    parsed = verify.parse(f"{algo}:{text}")
    if parsed is not None:
        return f"{parsed[0]}:{parsed[1]}"
    try:
        raw = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        return None
    if len(raw) != hashlib.new(algo).digest_size:
        return None
    return f"{algo}:{raw.hex()}"


def checksum_of(headers: Mapping[str, str]) -> str | None:
    """The strongest checksum a response header carries, if any.

    Prefer SHA-256 over MD5. For a direct URL the server is the only party
    that can vouch for the bytes, so a strong one is worth waiting for.
    """
    for name, algo in (
        ("X-Checksum-Sha256", "sha256"),
        ("X-Amz-Meta-Sha256", "sha256"),
        ("X-Amz-Checksum-Sha256", "sha256"),
        ("Content-MD5", "md5"),
    ):
        found = _as_hex(header_value(headers, name), algo)
        if found:
            return found
    return None


__all__ = ["Client", "parse_retry_after", "checksum_of", "header_value", "verify"]

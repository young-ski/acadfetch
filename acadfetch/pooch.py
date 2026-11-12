"""A pooch downloader that borrows acadfetch's HTTP client.

``pooch`` already caches, verifies and moves a file into place. What it does not
do is survive a link that stalls at 80%: its own downloader issues a flat GET,
and a drop means the whole transfer again. This adds the transport only — every
request goes through :class:`acadfetch.client.Client`, so it honours
``Retry-After``, retries a 5xx or a WAF block page, keeps a ``.part`` and resumes
it with ``Range``/``If-Range`` inside the attempt, and drops a credential header
when a redirect leaves the origin.

.. code-block:: python

    import pooch
    from acadfetch.pooch import downloader

    path = pooch.retrieve(url, known_hash="md5:…", downloader=downloader)

``pooch`` still does the hashing and the move, so this cannot make verification
weaker — only the arrival more likely. It is a plain callable rather than a
subclass because a callable is the whole contract ``pooch`` asks for.

Two limits worth knowing, both from how ``pooch`` drives a downloader:

* it hands each attempt a **fresh temporary file**, so a partial is resumed
  within one call and not across calls — ``pooch`` owns the retry loop between
  them;
* ``pooch``'s ``progressbar=True`` is implemented inside its own downloader and
  is not passed here, so a transfer through this one is silent. The CLI's
  progress line is a separate thing.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from . import atomic
from .client import Client
from .consts import DEFAULT_RATE, DEFAULT_RETRIES, DEFAULT_TIMEOUT
from .downloader import CHUNK, Options, fetch_file
from .errors import AcFError, HttpError, TargetError
from .sources.base import File

__all__ = ["Downloader", "downloader"]


class Downloader:
    """The callable ``pooch`` wants, configured like the CLI.

    Every argument maps to a flag of the same name, so a pipeline that already
    tuned the CLI does not have to learn a second vocabulary.
    """

    def __init__(
        self,
        *,
        rate: float = DEFAULT_RATE,
        retries: int = DEFAULT_RETRIES,
        timeout: float = DEFAULT_TIMEOUT,
        headers: Mapping[str, str] | None = None,
        block_retries: int = 2,
        allow_http: bool = False,
        fsync: bool = True,
    ) -> None:
        self.rate = rate
        self.retries = retries
        self.timeout = timeout
        self.headers = dict(headers or {})
        self.block_retries = block_retries
        self.allow_http = allow_http
        self.fsync = fsync

    def _client(self) -> Client:
        return Client(
            rate=self.rate,
            retries=self.retries,
            timeout=self.timeout,
            headers=self.headers,
            block_retries=self.block_retries,
            allow_http=self.allow_http,
        )

    # -- the two things pooch asks for -------------------------------------
    def __call__(
        self,
        url: str,
        output_file: Any = None,
        pooch: Any = None,
        check_only: bool = False,
    ) -> bool | None:
        if check_only:
            return self.available(url)
        if output_file is None:
            raise TargetError("pooch asked for a download but passed no output file")
        if hasattr(output_file, "write"):
            # A caller-supplied handle: pooch owns the file, so there is nothing
            # to rename and nothing worth resuming onto.
            self._stream(url, output_file)
            return None

        target = Path(output_file)
        target.parent.mkdir(parents=True, exist_ok=True)
        result = fetch_file(
            self._client(),
            File(url=url, name=target.name),
            Options(
                out=target.parent,
                concurrency=1,
                retries=self.retries,
                fsync=self.fsync,
            ),
            set(),
        )
        if result.state == "failed":
            # pooch does not catch this, so the sentence the CLI would print is
            # the sentence the caller sees.
            raise AcFError(result.error or f"could not fetch {url}")
        produced = Path(result.path or target)
        if produced != target:
            # `naming` is allowed to change a name; pooch's own file must win.
            atomic.replace(produced, target)
        return None

    def available(self, url: str) -> bool:
        """Whether the URL answers at all — HEAD first, one ranged byte after.

        ``Client.head`` treats a 403/405/501 as "no headers", which is
        indistinguishable from a 200 that sent none, so an empty answer falls
        through to a one-byte range request rather than being read as success.
        """
        client = self._client()
        try:
            if client.head(url):
                return True
        except HttpError:
            return False
        try:
            with client.open(url, {"Range": "bytes=0-0"}, attempts=1):
                return True
        except HttpError:
            return False

    def _stream(self, url: str, handle: Any) -> None:
        client = self._client()
        with client.open(url, attempts=self.retries + 1) as resp:
            reader = getattr(resp, "read1", None) or resp.read
            while True:
                chunk = reader(CHUNK)
                if not chunk:
                    break
                handle.write(chunk)
        handle.flush()


#: The zero-configuration instance: ``pooch.retrieve(..., downloader=downloader)``.
downloader = Downloader()

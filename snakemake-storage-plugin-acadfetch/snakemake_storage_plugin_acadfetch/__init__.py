"""Snakemake storage plugin: file URLs fetched through acadfetch.

Snakemake's own ``http`` plugin issues one flat GET per file, so a drop at 80%
costs the whole transfer. This one routes every request through acadfetch's
client, which gives a rule the transport the CLI already has: one process-wide
token bucket, ``Retry-After`` respected, 5xx and WAF block pages retried, and a
``.part`` resumed with ``Range``/``If-Range`` rather than restarted.

A query is a **file URL** — exactly the form ``acadfetch fetch <target> -w -``
prints for every file of a record, so a Zenodo record becomes workflow inputs
without anyone hand-copying ids::

    rule fetch:
        input:
            storage.acadfetch(
                "https://zenodo.org/records/123456/files/raw.tar.gz"
            )
        output:
            "raw.tar.gz"
        shell:
            "cp {input} {output}"

Settings are exposed as ``--storage-acadfetch-<name>`` on the Snakemake CLI.
"""

from __future__ import annotations

import email.utils
import hashlib
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import unquote, urlsplit

from snakemake_interface_common.exceptions import WorkflowError
from snakemake_interface_common.logging import get_logger
from snakemake_interface_storage_plugins.common import Operation
from snakemake_interface_storage_plugins.io import IOCacheStorageInterface, Mtime
from snakemake_interface_storage_plugins.settings import StorageProviderSettingsBase
from snakemake_interface_storage_plugins.storage_object import StorageObjectRead
from snakemake_interface_storage_plugins.storage_provider import (
    ExampleQuery,
    QueryType,
    StorageProviderBase,
    StorageQueryValidationResult,
)

from acadfetch import naming
from acadfetch.client import Client, header_value
from acadfetch.consts import DEFAULT_RATE, DEFAULT_RETRIES, DEFAULT_TIMEOUT
from acadfetch.downloader import fetch_one
from acadfetch.errors import AcFError

logger = get_logger()

#: One client per settings combination, so the token bucket is shared across every
#: storage object in the process. A client per object would multiply the rate by
#: the number of parallel rules, which is the opposite of what the limiter is for.
_CLIENTS: dict[tuple, Client] = {}


@dataclass
class StorageProviderSettings(StorageProviderSettingsBase):
    rate: float | None = field(
        default=None,
        metadata={"help": "Requests per second, global (default: 2)."},
    )
    retries: int | None = field(
        default=None,
        metadata={"help": "Retries per transfer on 5xx/429/network errors (default: 4)."},
    )
    timeout: float | None = field(
        default=None,
        metadata={"help": "Per-request timeout in seconds, connect and idle (default: 60)."},
    )
    header: str | None = field(
        default=None,
        metadata={"help": "Extra request header as 'Name: value', for a restricted record."},
    )


def _headers(raw: str | None) -> dict[str, str]:
    if not raw:
        return {}
    name, sep, value = raw.partition(":")
    if not sep:
        raise WorkflowError(f"--storage-acadfetch-header needs 'Name: value', got {raw!r}")
    return {name.strip(): value.strip()}


def _client_for(settings: Any) -> Client:
    """The shared client for these settings (``None`` fields mean "use the default")."""
    rate = getattr(settings, "rate", None) or DEFAULT_RATE
    retries = getattr(settings, "retries", None)
    timeout = getattr(settings, "timeout", None) or DEFAULT_TIMEOUT
    headers = _headers(getattr(settings, "header", None))
    key = (rate, retries, timeout, tuple(sorted(headers.items())))
    client = _CLIENTS.get(key)
    if client is None:
        client = Client(
            rate=rate,
            retries=DEFAULT_RETRIES if retries is None else retries,
            timeout=timeout,
            headers=headers,
        )
        _CLIENTS[key] = client
    return client


class StorageProvider(StorageProviderBase):
    def rate_limiter_key(self, query: str, operation: Operation) -> Any:
        return urlsplit(query).netloc

    @classmethod
    def example_queries(cls) -> list[ExampleQuery]:
        return [
            ExampleQuery(
                query="https://zenodo.org/records/123456/files/raw.tar.gz",
                description="A file URL, as printed by `acadfetch fetch <target> -w -`",
                type=QueryType.INPUT,
            )
        ]

    def default_max_requests_per_second(self) -> float:
        return DEFAULT_RATE

    def use_rate_limiter(self) -> bool:
        # acadfetch's Client owns one process-wide token bucket already. Letting
        # Snakemake throttle on top of it would silently halve the effective rate.
        return False

    @classmethod
    def is_valid_query(cls, query: str) -> StorageQueryValidationResult:
        try:
            parsed = urlsplit(query)
        except ValueError as exc:
            return StorageQueryValidationResult(
                query=query, valid=False, reason=f"cannot be parsed as a URL ({exc})"
            )
        if parsed.scheme not in ("http", "https"):
            return StorageQueryValidationResult(
                query=query,
                valid=False,
                reason=(
                    "a query must be a file URL (http or https). `acadfetch fetch "
                    "<doi|zenodo:id|arxiv:id> -w -` prints one per file of a record."
                ),
            )
        if not parsed.netloc:
            return StorageQueryValidationResult(query=query, valid=False, reason="URL has no host")
        return StorageQueryValidationResult(query=query, valid=True)

    def list_objects(self, query: Any) -> Iterable[str]:
        # One query is one file. Listing a record's files is what `-w/--urls` is
        # for, and doing it here would need a second network round trip per rule.
        raise NotImplementedError()


class StorageObject(StorageObjectRead):
    # -- naming ------------------------------------------------------------
    def local_suffix(self) -> str:
        """A readable, collision-free name: host, a query digest, the file name."""
        parts = urlsplit(self.query)
        name = naming.sanitize(unquote(parts.path.rsplit("/", 1)[-1]))
        host = naming.sanitize(parts.hostname or "host")
        digest = hashlib.sha256(self.query.encode("utf-8", "replace")).hexdigest()[:8]
        return f"{host}-{digest}-{name}"

    # -- metadata ----------------------------------------------------------
    def _client(self) -> Client:
        return _client_for(self.provider.settings)

    def _head(self) -> dict:
        try:
            return self._client().head(self.query)
        except AcFError as exc:
            logger.debug(f"HEAD {self.print_query} failed: {exc}")
            return {}

    def exists(self) -> bool:
        return self._client().available(self.query)

    def size(self) -> int:
        raw = header_value(self._head(), "Content-Length")
        try:
            return int(raw.strip()) if raw is not None else 0
        except (AttributeError, ValueError):
            return 0

    def mtime(self) -> float:
        """Last-Modified as a POSIX timestamp, or 0 when the server does not say.

        0 is the same choice Snakemake's http plugin makes: an object that cannot
        report its age is treated as old, so a local copy is not refetched on
        every run. Repository data is immutable, so that is the safe direction.
        """
        raw = header_value(self._head(), "Last-Modified")
        if not raw:
            return 0.0
        try:
            parsed = email.utils.parsedate_tz(raw)
            return float(email.utils.mktime_tz(parsed)) if parsed else 0.0
        except (TypeError, ValueError):
            return 0.0

    async def inventory(self, cache: IOCacheStorageInterface) -> None:
        name = self.local_path()
        headers = self._head()
        # A HEAD that loses its headers (403/405/501) is not proof of absence.
        present = bool(headers) or self.exists()
        cache.exists_in_storage[name] = present
        if present:
            cache.mtime[name] = Mtime(storage=self.mtime())
            cache.size[name] = self.size()

    def get_inventory_parent(self) -> str | None:
        # Nothing here can enumerate a directory; a URL is a single file.
        return None

    # -- transfer ----------------------------------------------------------
    def retrieve_object(self) -> None:
        settings = self.provider.settings
        retries = getattr(settings, "retries", None)
        try:
            fetch_one(
                self._client(),
                self.query,
                self.local_path(),
                retries=DEFAULT_RETRIES if retries is None else retries,
            )
        except AcFError as exc:
            raise WorkflowError(f"acadfetch could not fetch {self.print_query}: {exc}") from exc

    def cleanup(self) -> None:
        # The local copy is a cache entry, and Snakemake deletes it itself when
        # the rule declared no need to keep it. Deleting here would race that.
        pass

"""Types shared by every source adapter."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class File:
    url: str
    name: str
    size: int | None = None
    checksum: str | None = None
    #: ``"cli"`` when ``--checksum`` supplied it, otherwise ``None`` (the
    #: source published it, or nobody did).
    checksum_source: str | None = None


@dataclass
class Record:
    source: str
    target: str
    files: list[File] = field(default_factory=list)
    title: str = ""
    doi: str = ""
    license: str = ""
    publisher: str = ""
    version: str = ""
    landing: str = ""

    @property
    def total_bytes(self) -> int | None:
        if not self.files or any(f.size is None for f in self.files):
            return None
        return sum(f.size or 0 for f in self.files)


@dataclass
class Result:
    file: File
    state: str  # "downloaded" | "skipped" | "failed"
    path: str | None = None
    bytes: int = 0
    verified: bool | None = None
    error: str = ""
    resumed_from: int = 0
    #: Set when a failed transfer left a resumable ``.part`` behind.
    part: str | None = None
    #: The name actually used on disk, which may differ from ``file.name`` once
    #: duplicates are resolved. The manifest records this one.
    name: str | None = None
    #: Where the bytes actually came from, after redirects, and what the server
    #: said along the way. ``None`` when no response was read.
    final_url: str | None = None
    etag: str | None = None
    http_status: int | None = None
    #: RFC 3339 UTC, when the last byte (or the failure) arrived.
    retrieved_at: str | None = None

    @property
    def ok(self) -> bool:
        return self.state != "failed"

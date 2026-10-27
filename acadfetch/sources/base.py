"""Types shared by every source adapter."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class File:
    url: str
    name: str
    size: int | None = None
    checksum: str | None = None
    #: Tried in order when ``url`` is refused with a 403, e.g. a legacy
    #: endpoint for the same bytes.
    fallbacks: list[str] = field(default_factory=list)


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

    @property
    def ok(self) -> bool:
        return self.state != "failed"

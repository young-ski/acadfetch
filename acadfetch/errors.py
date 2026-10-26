"""Exception types. Every failure mode gets a distinct exit code path."""

from __future__ import annotations


class AcFError(Exception):
    """Base class for all acadfetch errors."""


class TargetError(AcFError):
    """The target string could not be parsed or resolved."""


class SourceError(AcFError):
    """A repository API returned an unusable response."""


class HttpError(AcFError):
    """An HTTP request failed after all retries.

    ``transient`` marks a failure that is worth retrying further up, currently
    only a 403 that came from a WAF/load-balancer block page rather than from
    the API's own permission check.
    """

    def __init__(self, status: int, url: str, detail: str = "", transient: bool = False) -> None:
        self.status = status
        self.url = url
        self.transient = transient
        super().__init__(f"HTTP {status} for {url}{': ' + detail if detail else ''}")


class VerifyError(AcFError):
    """Downloaded bytes do not match the checksum published by the source."""


class ManifestError(AcFError, ValueError):
    """The manifest is missing, unreadable, or not a manifest we understand.

    Also a ``ValueError`` so callers that used to catch that keep working.
    """


class PartialError(AcFError):
    """One or more files failed while others succeeded."""

    def __init__(self, failures) -> None:  # noqa: ANN001
        self.failures = list(failures)
        names = ", ".join(f.name for f in self.failures)
        super().__init__(f"{len(self.failures)} file(s) failed: {names}")

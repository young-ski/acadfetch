"""Filesystem calls that survive Windows sharing rules.

``os.replace`` gives content atomicity -- a reader sees old or new, never half --
but on NTFS it fails outright with ``WinError 5`` while any other handle has the
target open. Python's ``open()`` asks for read/write sharing but not
``FILE_SHARE_DELETE``, so "write a manifest while something is reading it" is a
coin flip. The bytes are safe; the call is not.

The fix is unglamorous: try again for a moment. Both helpers keep the tests
deterministic by accepting a ``sleep`` hook.
"""

from __future__ import annotations

import contextlib
import os
import time
from pathlib import Path

# Long enough to outlast a reader that opened the file a moment ago, short enough
# that a genuinely locked file still fails during one command.
ATTEMPTS = 6
DELAY = 0.05


def _retry(fn, *args, attempts: int = ATTEMPTS, delay: float = DELAY):  # noqa: ANN001, ANN202
    last: OSError | None = None
    for attempt in range(attempts):
        try:
            return fn(*args)
        except PermissionError as exc:  # WinError 5 / EACCES while someone reads
            last = exc
        except OSError as exc:
            if getattr(exc, "winerror", None) not in (5, 32, 33):
                raise
            last = exc
        if attempt < attempts - 1:
            time.sleep(delay * (1 + attempt))
    assert last is not None
    raise last


def replace(src: Path, dst: Path) -> None:
    """``os.replace`` with a short retry for Windows sharing violations."""
    _retry(os.replace, src, dst)


def read_text(path: Path, encoding: str = "utf-8") -> str:
    """Read a file that someone may be replacing underneath us."""
    return _retry(path.read_text, encoding)


def unlink(path: Path) -> None:
    """Remove a file that may be open elsewhere; a lost race is not an error."""
    with contextlib.suppress(OSError):
        _retry(Path.unlink, path)

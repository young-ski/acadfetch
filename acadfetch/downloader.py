"""The transfer engine.

Resume is built on three ideas:

* bytes always land in ``<name>.part`` and are renamed only after verification,
  so a killer ``Ctrl-C`` never leaves a truncated file looking finished;
* the validator (ETag / Last-Modified) lives in a sidecar so ``If-Range`` can
  prove the remote object did not change between runs;
* the retry loop re-uses the partial file instead of restarting, which makes a
  mid-stream socket drop cost at most one chunk.
"""

from __future__ import annotations

import contextlib
import fnmatch
import hashlib
import json
import os
import random
import shutil
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.client import HTTPException
from pathlib import Path
from typing import cast

from . import atomic, debug, naming, verify
from .client import Client
from .consts import CHUNK
from .errors import HttpError, LocalError, PartialError, TargetError
from .progress import Progress
from .sources.base import File, Record, Result
from .units import human

TRANSFER_ATTEMPTS = 3
#: ``IncompleteRead`` is an ``HTTPException``, not an ``OSError``: a body cut
#: short mid-stream used to escape the retry loop and the CLI as a traceback.
HTTP_EXC = (HTTPException,)
#: Extensions that are never legitimately served as text/html: an HTML body for
#: one of these is a WAF block page or a login redirect, not the file.
_BINARY_EXT = frozenset(
    {
        ".pdf",
        ".zip",
        ".tar",
        ".gz",
        ".bz2",
        ".xz",
        ".zst",
        ".7z",
        ".rar",
        ".csv",
        ".tsv",
        ".parquet",
        ".npy",
        ".npz",
        ".h5",
        ".hdf5",
        ".fits",
        ".docx",
        ".xlsx",
        ".pptx",
        ".bin",
        ".dat",
        ".nc",
        ".sav",
        ".dta",
    }
)
META_SUFFIX = ".part.meta.json"
LOCK_NAME = ".acadfetch.lock"


@dataclass(frozen=True)
class Transfer:
    """What one attempt actually did, for the manifest's provenance fields."""

    written: int = 0
    final_url: str | None = None
    etag: str | None = None
    http_status: int | None = None
    retrieved_at: str | None = None
    #: Algorithm -> hex digest, computed while the bytes went by. ``None`` when
    #: no body was read (a 416 on a complete part).
    digests: dict[str, str] | None = None


@dataclass
class Options:
    out: Path
    concurrency: int = 3
    retries: int = TRANSFER_ATTEMPTS
    dry_run: bool = False
    max_bytes: int | None = None
    progress: Progress | None = None
    verify_only: bool = False
    #: Set by :func:`fetch_record` when a Ctrl-C cut the run short.
    interrupted: bool = False
    #: Flush each finished ``.part`` to the platter before it is renamed.
    fsync: bool = True
    #: Re-download even when a matching file is already there.
    force: bool = False
    #: ``-o`` was given, so the directory is the user's choice, not a guess.
    explicit_out: bool = False
    #: name -> url recorded by an earlier manifest in ``out``, filled by
    #: :func:`fetch_record`. Without a checksum it is what ties a file to a URL.
    trusted: dict[str, str] = field(default_factory=dict)


# Live responses, so an interrupt can abort a read that is blocked on a socket
# instead of waiting out the 60 s timeout for each one.
_LIVE: set = set()
_LIVE_LOCK = threading.Lock()
_STOP = threading.Event()


def _abort_open_transfers() -> None:
    with _LIVE_LOCK:
        live = list(_LIVE)
    for resp in live:
        with contextlib.suppress(Exception):  # noqa: BLE001 - closing is best effort
            resp.close()


def _now() -> str:
    """RFC 3339 UTC, second resolution: what the manifest records."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_meta(path: Path) -> dict:
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def _write_meta(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), "utf-8")
    atomic.replace(tmp, path)


def _kept_bytes(part: Path) -> int:
    """Size of a partial worth resuming; 0 when there is nothing to keep."""
    try:
        return part.stat().st_size
    except OSError:
        return 0


def _drop(*paths: Path) -> None:
    for path in paths:
        atomic.unlink(path)


def _content_length(headers, url: str) -> int | None:
    raw = headers.get("Content-Length")
    if raw is None:
        return None
    try:
        return int(raw.strip())
    except (AttributeError, ValueError):
        raise HttpError(0, url, f"unparseable Content-Length: {raw!r}") from None


def _range_start(value: str | None) -> int | None:
    """First byte offset of a ``Content-Range: bytes a-b/n`` header."""
    if not value:
        return None
    head, _, _ = value.strip().partition("-")
    _, _, start = head.partition("bytes")
    try:
        return int(start.strip())
    except ValueError:
        return None


def _socket_of(resp) -> socket.socket | None:  # noqa: ANN001
    """The socket under a urllib response, if we can reach it."""
    raw = getattr(getattr(resp, "fp", None), "raw", None)
    return cast("socket.socket | None", getattr(raw, "_sock", None))


def _is_error_page(content_type: str, name: str) -> bool:
    """True when an HTML body cannot be the file that was asked for."""
    if "html" not in content_type.lower():
        return False
    return Path(name).suffix.lower() in _BINARY_EXT


def _clear_timeout_latch(resp) -> None:  # noqa: ANN001
    """Re-arm a response whose socket timeout already fired.

    ``socket.SocketIO`` sets ``_timeout_occurred`` on the first ``timeout`` and
    then raises ``OSError("cannot read from timed out object")`` from every
    later read, forever -- even though the socket itself is healthy. That is
    what made a single stalled read end the transfer: nothing can unblock a
    blocked read from another thread on Windows (closing does not), so the poll
    has to keep firing, and the latch has to be cleared for the stream to stay
    usable.

    The attribute is private, so a missing one simply restores the old
    behaviour instead of breaking the run.
    """
    stream = getattr(getattr(resp, "fp", None), "raw", None)
    if stream is not None and getattr(stream, "_timeout_occurred", False):
        stream._timeout_occurred = False


def _chunks(resp, idle_timeout: float, url: str):  # noqa: ANN001, ANN202
    """Yield body chunks, polling so an interrupt lands quickly.

    ``read1`` never waits for a full buffer, so a timeout costs no bytes; it is
    only present on buffered readers, so plain ``read`` stays as a fallback.
    """
    reader = getattr(resp, "read1", None) or resp.read
    sock = _socket_of(resp)
    poll = 1.0 if idle_timeout > 1.0 else idle_timeout
    if sock is not None:
        with contextlib.suppress(OSError):
            sock.settimeout(poll)
    deadline = time.monotonic() + idle_timeout
    while True:
        if _STOP.is_set():
            raise HttpError(0, url, "interrupted")
        try:
            chunk = reader(CHUNK)
        except TimeoutError:
            if time.monotonic() > deadline:
                raise
            _clear_timeout_latch(resp)
            continue
        if not chunk:
            return
        deadline = time.monotonic() + idle_timeout
        yield chunk


def _star_total(content_range: str | None) -> int | None:
    """``bytes */N`` → N, for the 416 that carries it."""
    if not content_range:
        return None
    _, _, value = content_range.strip().partition("*/")
    try:
        return int(value.strip())
    except ValueError:
        return None


def _restart_or_finish(
    file: File, part: Path, meta_path: Path, have: int, exc: HttpError
) -> Transfer:
    """What a 416 means: the part is the file, or it is stale and must go.

    "Already complete" used to be assumed whenever the part existed, so a stale
    part (a different, older object) was renamed into place with nothing
    checked. A matching checksum is proof; otherwise the size the server
    reports for the whole object has to equal the part exactly.
    """
    parsed = verify.parse(file.checksum)
    digests: dict[str, str] | None = None
    if parsed is not None:
        digests = verify.hashes(part, {"sha256", parsed[0]})
        if digests[parsed[0]] == parsed[1]:
            return Transfer(0, digests=digests)
    total = _star_total(exc.content_range)
    if total is not None and total == have:
        if digests is None:
            digests = verify.hashes(part, {"sha256"})
        return Transfer(0, digests=digests)
    _drop(part, meta_path)
    if total is None:
        raised, compared = "416 without a size to compare against", "no evidence"
    else:
        raised, compared = f"server says {total} bytes", f"the .part has {have}"
    raise HttpError(416, file.url, f"{raised}, {compared}; restarting", transient=True)


def _transfer(client: Client, file: File, part: Path, meta_path: Path, opts: Options) -> Transfer:
    """Move bytes into ``part``, resuming if possible. Returns what happened."""
    meta = _read_meta(meta_path) if part.exists() else {}
    if meta.get("url") not in (None, file.url):
        _drop(part, meta_path)
        meta = {}

    have = part.stat().st_size if part.exists() else 0
    if file.size is not None and have > file.size:
        _drop(part, meta_path)
        have = 0

    headers = {}
    if have:
        headers["Range"] = f"bytes={have}-"
        validator = meta.get("etag") or meta.get("last_modified")
        if validator:
            headers["If-Range"] = validator

    written = 0
    # One attempt here: the caller owns the retry budget for bytes, because it
    # is the one that knows about resume points and fallback URLs.
    try:
        resp = client.open(file.url, headers, attempts=1)
    except HttpError as exc:
        if exc.status == 416 and have:
            return _restart_or_finish(file, part, meta_path, have, exc)
        raise

    with resp:
        status = getattr(resp, "status", 200)
        content_type = resp.headers.get("Content-Type") or ""
        if _is_error_page(content_type, file.name):
            # Say what it is instead of baking the markup into a .pdf and
            # calling that finished, which also makes the next run skip it.
            raise HttpError(
                0,
                file.url,
                f"server answered {content_type.split(';')[0]} for {file.name!r} "
                f"(an error or login page, not the file)",
                reason="html-page",
            )
        declared = _content_length(resp.headers, file.url)
        whole = True
        if have and status == 206:
            # Decide whether this body is the remaining bytes or the whole file
            # again. Content-Range is authoritative; without it the declared
            # length tells us (a full-size body means the range was ignored);
            # with neither, restart rather than splice blindly.
            start = _range_start(resp.headers.get("Content-Range"))
            if start is not None:
                whole = start != have
            else:
                whole = declared is None or file.size is None or declared != file.size - have
        if whole:
            mode, offset = "wb", 0
        else:
            mode, offset = "ab", have

        # Hash while the bytes go by instead of reading the finished file again.
        # A resumed transfer has to be fed what is already on disk first.
        parsed = verify.parse(file.checksum)
        wanted_algo = parsed[0] if parsed else None
        hashers = {"sha256": hashlib.sha256()}
        if wanted_algo and wanted_algo != "sha256":
            hashers[wanted_algo] = hashlib.new(wanted_algo)
        if mode == "ab":
            with part.open("rb") as existing:
                for old in iter(lambda: existing.read(1 << 20), b""):
                    for hasher in hashers.values():
                        hasher.update(old)
        _write_meta(
            meta_path,
            {
                "url": file.url,
                "etag": resp.headers.get("ETag"),
                "last_modified": resp.headers.get("Last-Modified"),
                "size": file.size,
            },
        )
        if declared is not None and file.size is not None:
            expected = file.size - offset
            if declared != expected:
                raise HttpError(
                    0,
                    file.url,
                    f"Content-Length {declared} disagrees with the published size "
                    f"({expected} bytes still expected)",
                )
        # Reads poll with a short socket timeout so an interrupt is noticed
        # within a second instead of after the whole 60 s socket timeout, and
        # so an idle connection fails on its own budget. Closing the response
        # from another thread additionally kicks a read that is already
        # blocked.
        with _LIVE_LOCK:
            _LIVE.add(resp)
        try:
            with part.open(mode) as fh:
                for chunk in _chunks(resp, client.timeout, file.url):
                    fh.write(chunk)
                    for hasher in hashers.values():
                        hasher.update(chunk)
                    written += len(chunk)
                    if opts.progress:
                        opts.progress.advance(len(chunk))
                    if opts.max_bytes and offset + written > opts.max_bytes:
                        raise HttpError(
                            0,
                            file.url,
                            f"refused locally: over --max-size ({human(opts.max_bytes)})",
                            reason="max-size",
                        )
                if opts.fsync:
                    # The rename below is atomic, but only against crashes that
                    # leave the page cache intact. This is what makes the bytes
                    # survive a power cut before they get a name.
                    fh.flush()
                    os.fsync(fh.fileno())
        finally:
            with _LIVE_LOCK:
                _LIVE.discard(resp)
        if declared is not None and declared != written:
            raise HttpError(0, file.url, f"read {written} bytes, response declared {declared}")
        return Transfer(
            written=written,
            final_url=resp.geturl() or file.url,
            etag=resp.headers.get("ETag"),
            http_status=status,
            retrieved_at=_now(),
            digests={algo: digest.hexdigest() for algo, digest in hashers.items()},
        )


DISK_MARGIN = 64 * 1024 * 1024


def _existing(path: Path) -> Path:
    """The nearest directory that is there yet -- ``-o`` is created later."""
    for candidate in (path, *path.parents):
        if candidate.is_dir():
            return candidate
    return path


def _trusted_urls(manifest_path: Path) -> dict[str, str]:
    """name -> url pairs an earlier manifest in the same directory recorded."""
    try:
        data = json.loads(manifest_path.read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    rows = data.get("files") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return {}
    return {
        row["name"]: str(row.get("url") or "")
        for row in rows
        if isinstance(row, dict) and isinstance(row.get("name"), str)
    }


def check_disk(
    files, out: Path, *, margin: int = DISK_MARGIN, strict: bool = True
) -> tuple[int, int]:  # noqa: ANN001
    """Refuse a run that cannot fit, before it spends a night on 97% of it.

    Returns ``(free, needed)``. Only files with a published size can be counted;
    one unknown size makes the total a lower bound, which is still worth
    checking. Files already complete or partly fetched are subtracted -- a
    resumed run needs the remainder, not the whole record again.
    """
    needed = 0
    for file in files:
        if file.size is None:
            continue
        name = naming.sanitize(file.name)
        final = out / name
        part = out / (name + ".part")
        if final.exists():
            continue
        needed += file.size - (part.stat().st_size if part.exists() else 0)
    if needed <= 0:
        return (0, 0)
    free = shutil.disk_usage(_existing(out)).free
    if strict and free < needed + margin:
        raise LocalError(
            f"needs {human(needed)} but {human(free)} is free in {out} "
            f"(keeps {human(margin)} spare; free space or choose -o)"
        )
    return (free, needed)


def _pid_alive(pid: int) -> bool:
    """Is ``pid`` still running, without disturbing it?

    ``os.kill(pid, 0)`` is the POSIX idiom, but on Windows it sends a console
    CTRL_C: probing our own pid interrupts the process asking the question.
    """
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # query limited info
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # exists, owned by someone else
        return True
    except OSError:
        return False
    return True


def _take_lock(out: Path) -> Path:
    """Refuse to share the directory with a live concurrent run.

    Two runs into one ``-o`` interleave writes into the same ``.part`` and the
    survivor is a file neither of them wrote. The lock records the owner; a dead
    owner, or one we cannot read, is taken over.
    """
    lock = out / LOCK_NAME
    for _ in range(2):
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            owner = 0
            try:
                owner = int(json.loads(lock.read_text("utf-8")).get("pid") or 0)
            except (OSError, ValueError):
                owner = 0
            # A live owner is another run, even when it is this pid: two
            # fetches into one directory in one process clash just as badly.
            # Only a dead or unreadable owner is stale.
            if owner and _pid_alive(owner):
                raise TargetError(
                    f"{out} is being written by pid {owner}; wait for it, or "
                    f"delete {lock} if that process is gone"
                ) from None
            atomic.unlink(lock)
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"pid": os.getpid(), "started": _now()}, fh)
        return lock
    raise TargetError(f"could not take the lock in {out}; is another run active?")


def _release_lock(lock: Path) -> None:
    try:
        if int(json.loads(lock.read_text("utf-8")).get("pid") or 0) != os.getpid():
            return
    except (OSError, ValueError):
        pass
    atomic.unlink(lock)


def _reserved(files) -> set[str]:  # noqa: ANN001
    """Names the tool itself will write, so a repository file cannot take them.

    A file the repository happens to call ``manifest.json`` was overwritten by
    our manifest, and a file called ``x.part`` was swallowed by ``x``'s sidecar.
    Reserving the derived names is cheaper than teaching every later step to
    dodge them.
    """
    reserved = {"manifest.json", "manifest.json.tmp", LOCK_NAME}
    for file in files:
        name = naming.sanitize(file.name).lower()
        reserved.add(name + ".part")
        reserved.add(name + META_SUFFIX)
    return reserved


def _verdict(file: File, digests: dict[str, str] | None) -> bool | None:
    parsed = verify.parse(file.checksum)
    if parsed is None:
        return None
    if not digests:
        return None
    return digests.get(parsed[0]) == parsed[1]


def _already_there(
    final: Path, name: str, file: File, opts: Options
) -> tuple[bool, dict[str, str] | None]:
    """Whether an existing file may stand in for this one, and its digests.

    A checksum match is proof. Without a checksum, a size match only means the
    bytes *could* be the same, so one more thing must tie the file to this URL:
    an explicit ``-o`` (the user chose the directory) or a manifest in it that
    recorded the same URL. Both hashes are computed in that one read, so the
    manifest does not have to read the file again.
    """
    if opts.force or not final.exists():
        return False, None
    parsed = verify.parse(file.checksum)
    algos = {"sha256"} | ({parsed[0]} if parsed else set())
    digests = verify.hashes(final, algos)
    if parsed is not None:
        return digests[parsed[0]] == parsed[1], digests
    if file.size is not None and final.stat().st_size != file.size:
        return False, digests
    if opts.explicit_out:
        return True, digests
    return opts.trusted.get(name) == file.url, digests


def fetch_file(client: Client, file: File, opts: Options, taken: set[str]) -> Result:
    name = naming.unique(naming.sanitize(file.name), taken)
    final = opts.out / name
    part = opts.out / (name + ".part")
    meta_path = opts.out / (name + META_SUFFIX)

    trusted, digests = _already_there(final, name, file, opts)
    if trusted:
        return Result(
            file=file,
            state="skipped",
            path=str(final),
            bytes=final.stat().st_size,
            verified=_verdict(file, digests),
            name=name,
            sha256=(digests or {}).get("sha256"),
        )

    if opts.dry_run:
        return Result(file=file, state="skipped", path=str(final), verified=None, name=name)

    resumed_from = part.stat().st_size if part.exists() else 0
    progress = opts.progress
    if progress:
        progress.start(name, file.size)
    try:
        res = _attempt(client, file, part, meta_path, final, opts, resumed_from)
        res.name = name
        return res
    except KeyboardInterrupt:
        # Raise inside a read on the main thread: the bytes already paid for are
        # worth reporting, and the run has to stop rather than move to the next
        # file.
        _STOP.set()
        opts.interrupted = True
        kept = _kept_bytes(part)
        return Result(
            file=file,
            state="failed",
            error="interrupted",
            bytes=kept,
            part=str(part) if kept else None,
            name=name,
        )
    finally:
        if progress:
            progress.stop(name)


def _attempt(
    client: Client,
    file: File,
    part: Path,
    meta_path: Path,
    final: Path,
    opts: Options,
    resumed_from: int,
) -> Result:
    """Retry loop, verification and final rename for one file.

    A 403 that looks like a CDN block page is retried a couple of times with the
    partial file kept. Everything else terminal fails immediately.
    """
    last: Exception | None = None
    info: Transfer | None = None
    blocked = 0
    for attempt in range(opts.retries + 1):
        try:
            info = _transfer(client, file, part, meta_path, opts)
            break
        except HttpError as exc:
            last = exc
            if exc.reason == "html-page":
                # Markup is not the file, so it must not be resumed onto.
                _drop(part, meta_path)
            if exc.reason == "max-size":
                # Do not leave an over-limit part for the next run to
                # resume straight back into the same wall.
                _drop(part, meta_path)
                return Result(
                    file=file,
                    state="failed",
                    error=str(exc),
                    http_status=exc.status,
                    retrieved_at=_now(),
                )
            if exc.status == 403 and exc.transient and blocked < client.block_retries:
                blocked += 1
            elif exc.status in (401, 403, 404, 410):
                break  # terminal
        except (*HTTP_EXC, OSError, ConnectionError, ValueError) as exc:
            # ValueError: the response was closed under us by an interrupt.
            last = exc
        if _STOP.is_set():
            return Result(file=file, state="failed", error="interrupted")
        if attempt < opts.retries:
            time.sleep(min(2**attempt, 10) * (0.7 + random.random() * 0.6))
            resumed_from = part.stat().st_size if part.exists() else 0
    else:
        assert last is not None  # the loop only ends after a failure
        return Result(
            file=file,
            state="failed",
            error=f"{last}{_hint(last)}",
            bytes=_kept_bytes(part),
            part=str(part) if _kept_bytes(part) else None,
            http_status=getattr(last, "status", None),
            retrieved_at=_now(),
        )

    if last is not None and not part.exists():
        return Result(
            file=file,
            state="failed",
            error=str(last),
            http_status=getattr(last, "status", None),
            retrieved_at=_now(),
        )

    if file.size is not None and part.exists() and part.stat().st_size != file.size:
        return Result(
            file=file,
            state="failed",
            error=f"size mismatch (want {file.size})",
            bytes=part.stat().st_size,
            http_status=info.http_status if info else None,
            retrieved_at=_now(),
        )

    verdict = _verdict(file, info.digests if info else None)
    if verdict is False:
        _drop(part, meta_path)
        try:
            info = _transfer(client, file, part, meta_path, opts)
            verdict = _verdict(file, info.digests if info else None)
        except Exception as exc:  # noqa: BLE001
            return Result(
                file=file,
                state="failed",
                error=f"re-download failed: {exc}{_hint(exc)}",
                http_status=getattr(exc, "status", None),
                retrieved_at=_now(),
            )
        if verdict is False:
            _drop(part, meta_path)
            # verified=False is what makes the mismatch legible downstream, not
            # only in the sentence.
            return Result(file=file, state="failed", error="checksum mismatch", verified=False)

    atomic.replace(part, final)
    _drop(meta_path)
    return Result(
        file=file,
        state="downloaded",
        path=str(final),
        bytes=final.stat().st_size,
        verified=verdict,
        resumed_from=resumed_from,
        sha256=(info.digests or {}).get("sha256") if info else None,
        final_url=info.final_url if info else None,
        etag=info.etag if info else None,
        http_status=info.http_status if info else None,
        retrieved_at=info.retrieved_at if info else _now(),
    )


def matches(file: File, only: list[str] | None, globs: list[str] | None) -> bool:
    """Whether a file passes the ``--only`` / ``--glob`` filters."""
    name = naming.sanitize(file.name).lower()
    if (
        only
        and name not in {n.lower() for n in only}
        and file.name.lower() not in {n.lower() for n in only}
    ):
        return False
    return not globs or any(fnmatch.fnmatch(name, glob.lower()) for glob in globs)


def fetch_record(
    record: Record,
    opts: Options,
    client: Client,
    only: list[str] | None = None,
    globs: list[str] | None = None,
) -> list[Result]:
    """Fetch every file of ``record``, surviving a Ctrl-C.

    The stop flag is process-wide, so it is cleared on the way out as well as in:
    a later run in the same process must not start out believing it is halted.
    """
    _STOP.clear()
    try:
        return _fetch_record(record, opts, client, only, globs)
    finally:
        _STOP.clear()


def _fetch_record(
    record: Record,
    opts: Options,
    client: Client,
    only: list[str] | None = None,
    globs: list[str] | None = None,
) -> list[Result]:
    if opts.out.exists() and not opts.out.is_dir():
        raise TargetError(f"-o must be a directory, and {opts.out} is a file")
    opts.out.mkdir(parents=True, exist_ok=True)
    files = record.files
    if only or globs:
        files = [f for f in files if matches(f, only, globs)]
        if not files:
            raise PartialError([])

    if opts.max_bytes:
        check_total(record, opts.max_bytes)
    lock: Path | None = None
    if not opts.dry_run:
        check_disk(files, opts.out)
        lock = _take_lock(opts.out)
        opts.trusted = _trusted_urls(opts.out / "manifest.json")

    taken = _reserved(files)
    results: list[Result] = []
    opts.interrupted = False

    try:
        return _drain(files, record, opts, client, taken, results)
    finally:
        if lock is not None:
            _release_lock(lock)


def _drain(files, record, opts, client, taken, results):  # noqa: ANN001, ANN202
    if opts.concurrency <= 1:
        for file in files:
            try:
                results.append(fetch_file(client, file, opts, taken))
            except KeyboardInterrupt:
                # Raised between files rather than inside a read.
                opts.interrupted = True
                _STOP.set()
                results.append(Result(file=file, state="failed", error="interrupted"))
                break
            except Exception as exc:  # noqa: BLE001 - one file must not end the batch
                results.append(_crashed(file, exc))
            if opts.interrupted:
                break
        return _in_record_order(results, record)

    pool = ThreadPoolExecutor(max_workers=opts.concurrency)
    # Keep the in-flight set bounded: a 5000-file record submitted in one go
    # holds 5000 futures and progress keys at once.
    window = max(opts.concurrency * 2, 1)
    queue = iter(files)
    futures = {}
    name_lock = threading.Lock()

    def submit(file: File) -> None:  # noqa: ANN001
        with name_lock:
            futures[pool.submit(fetch_file, client, file, opts, taken)] = file

    try:
        for file in queue:
            submit(file)
            if len(futures) >= window:
                break
        while futures:
            for future in as_completed(list(futures)):
                file = futures.pop(future)
                try:
                    results.append(future.result())
                except Exception as exc:  # noqa: BLE001 - one worker must not end the batch
                    results.append(_crashed(file, exc))
                nxt = next(queue, None)
                if nxt is not None:
                    submit(nxt)
    except KeyboardInterrupt:
        # Abort the reads that are blocked, then let the workers unwind: they
        # see the stop flag and keep their .part instead of retrying.
        opts.interrupted = True
        _STOP.set()
        _abort_open_transfers()
        pool.shutdown(wait=True, cancel_futures=True)
        for future, file in futures.items():
            if future.cancelled() or not future.done():
                continue
            try:
                results.append(future.result())
            except KeyboardInterrupt:
                results.append(Result(file=file, state="failed", error="interrupted"))
            except Exception:  # noqa: BLE001 - one bad worker must not hide the rest
                pass
    else:
        pool.shutdown(wait=True)
    return _in_record_order(results, record)


def _crashed(file: File, exc: Exception) -> Result:
    """An unexpected worker exception, as one failed row instead of a traceback."""
    debug.log_exception(exc)
    return Result(
        file=file,
        state="failed",
        error=f"{type(exc).__name__}: {exc}",
        retrieved_at=_now(),
    )


def _in_record_order(results: list[Result], record: Record) -> list[Result]:
    order = {id(f): i for i, f in enumerate(record.files)}
    return sorted(results, key=lambda r: order.get(id(r.file), 0))


def _hint(exc: Exception) -> str:
    """The next command, appended to an error that a user can act on."""
    text = str(exc).lower()
    if isinstance(exc, TimeoutError) or "timed out" in text:
        return " - the link stalled; rerun to resume, or try --rate 1 -j 1"
    if "getaddrinfo" in text or "name or service not known" in text:
        return " - name resolution failed; check DNS, VPN or proxy settings"
    if getattr(exc, "status", None) == 403:
        return " - if a WAF is involved, retry with --retry-403 2"
    return ""


def check_total(record: Record, limit: int) -> None:
    total = record.total_bytes
    if total is None:
        return
    if total > limit:
        from .errors import SourceError

        raise SourceError(f"record is {human(total)}, over --max-size ({human(limit)})")

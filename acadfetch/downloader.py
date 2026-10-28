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
import json
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from http.client import HTTPException
from pathlib import Path

from . import atomic, naming, verify
from .client import Client
from .consts import CHUNK
from .errors import HttpError, PartialError
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


def _socket_of(resp) -> object | None:  # noqa: ANN001
    """The socket under a urllib response, if we can reach it."""
    raw = getattr(getattr(resp, "fp", None), "raw", None)
    return getattr(raw, "_sock", None)


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
    if getattr(stream, "_timeout_occurred", False):
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


def _transfer(client: Client, file: File, part: Path, meta_path: Path, opts: Options) -> int:
    """Move bytes into ``part``, resuming if possible. Returns bytes written."""
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
    resumed = 0
    # One attempt here: the caller owns the retry budget for bytes, because it
    # is the one that knows about resume points and fallback URLs.
    try:
        resp = client.open(file.url, headers, attempts=1)
    except HttpError as exc:
        if exc.status == 416 and have:
            return 0  # already complete
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
            mode, offset, resumed = "ab", have, have
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
        finally:
            with _LIVE_LOCK:
                _LIVE.discard(resp)
        if declared is not None and declared != written:
            raise HttpError(0, file.url, f"read {written} bytes, response declared {declared}")
    if resumed:
        return written
    return written


def fetch_file(client: Client, file: File, opts: Options, taken: set[str]) -> Result:
    name = naming.unique(naming.sanitize(file.name), taken)
    final = opts.out / name
    part = opts.out / (name + ".part")
    meta_path = opts.out / (name + META_SUFFIX)

    if final.exists():
        verdict = verify.matches(final, file.checksum)
        sized_ok = file.size is None or final.stat().st_size == file.size
        if verdict is True or (verdict is None and sized_ok):
            return Result(
                file=file,
                state="skipped",
                path=str(final),
                bytes=final.stat().st_size,
                verified=verdict,
                name=name,
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

    A 403 that looks like a WAF block page is retried a couple of times with
    the partial file kept, then the file's fallback URLs are tried (a legacy
    endpoint for the same bytes). Everything else terminal fails immediately.
    """
    last: Exception | None = None
    blocked = 0
    candidates = [file.url, *file.fallbacks]
    done = False
    for index, url in enumerate(candidates):
        source = file if url == file.url else replace(file, url=url)
        for attempt in range(opts.retries + 1):
            try:
                _transfer(client, source, part, meta_path, opts)
                done = True
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
                    return Result(file=file, state="failed", error=str(exc))
                if exc.status == 403 and exc.transient and blocked < client.block_retries:
                    blocked += 1
                elif exc.status in (401, 403, 404, 410):
                    break  # terminal for this URL; a fallback may still work
            except (*HTTP_EXC, OSError, ConnectionError, ValueError) as exc:
                # ValueError: the response was closed under us by an interrupt.
                last = exc
            if _STOP.is_set():
                return Result(file=file, state="failed", error="interrupted")
            if attempt < opts.retries:
                time.sleep(min(2**attempt, 10) * (0.7 + random.random() * 0.6))
                resumed_from = part.stat().st_size if part.exists() else 0
        if done:
            break
        if index < len(candidates) - 1:
            _drop(part, meta_path)
            resumed_from = 0
            continue
        return Result(
            file=file,
            state="failed",
            error=f"{last}{_hint(last)}",
            bytes=_kept_bytes(part),
            part=str(part) if _kept_bytes(part) else None,
        )

    if last is not None and not part.exists():
        return Result(file=file, state="failed", error=str(last))

    if file.size is not None and part.exists() and part.stat().st_size != file.size:
        return Result(
            file=file,
            state="failed",
            error=f"size mismatch (want {file.size})",
            bytes=part.stat().st_size,
        )

    verdict = verify.matches(part, file.checksum)
    if verdict is False:
        _drop(part, meta_path)
        try:
            _transfer(client, file, part, meta_path, opts)
            verdict = verify.matches(part, file.checksum)
        except Exception as exc:  # noqa: BLE001
            return Result(file=file, state="failed", error=f"re-download failed: {exc}{_hint(exc)}")
        if verdict is False:
            _drop(part, meta_path)
            return Result(file=file, state="failed", error="checksum mismatch")

    atomic.replace(part, final)
    _drop(meta_path)
    return Result(
        file=file,
        state="downloaded",
        path=str(final),
        bytes=final.stat().st_size,
        verified=verdict,
        resumed_from=resumed_from,
    )


def fetch_record(
    record: Record, opts: Options, client: Client, only: list[str] | None = None
) -> list[Result]:
    """Fetch every file of ``record``, surviving a Ctrl-C.

    The stop flag is process-wide, so it is cleared on the way out as well as in:
    a later run in the same process must not start out believing it is halted.
    """
    _STOP.clear()
    try:
        return _fetch_record(record, opts, client, only)
    finally:
        _STOP.clear()


def _fetch_record(
    record: Record, opts: Options, client: Client, only: list[str] | None = None
) -> list[Result]:
    opts.out.mkdir(parents=True, exist_ok=True)
    files = record.files
    if only:
        wanted = {n.lower() for n in only}
        files = [
            f
            for f in files
            if naming.sanitize(f.name).lower() in wanted or f.name.lower() in wanted
        ]
        if not files:
            raise PartialError([])

    if opts.max_bytes:
        check_total(record, opts.max_bytes)

    taken: set[str] = set()
    lock = threading.Lock()
    results: list[Result] = []
    opts.interrupted = False

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
            if opts.interrupted:
                break
        return _in_record_order(results, record)

    pool = ThreadPoolExecutor(max_workers=opts.concurrency)
    futures = {}
    try:
        for file in files:
            with lock:
                futures[pool.submit(fetch_file, client, file, opts, taken)] = file
        for future in as_completed(futures):
            results.append(future.result())
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

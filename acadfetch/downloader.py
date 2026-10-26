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
import os
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from pathlib import Path

from . import naming, verify
from .client import Client
from .consts import CHUNK
from .errors import HttpError, PartialError
from .progress import Progress
from .sources.base import File, Record, Result
from .units import human

TRANSFER_ATTEMPTS = 3
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


def _read_meta(path: Path) -> dict:
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def _write_meta(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), "utf-8")
    os.replace(tmp, path)


def _drop(*paths: Path) -> None:
    for path in paths:
        with contextlib.suppress(OSError):
            path.unlink()


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
        with part.open(mode) as fh:
            while True:
                chunk = resp.read(CHUNK)
                if not chunk:
                    break
                fh.write(chunk)
                written += len(chunk)
                if opts.progress:
                    opts.progress.advance(len(chunk))
                if opts.max_bytes and offset + written > opts.max_bytes:
                    raise HttpError(0, file.url, f"exceeds --max-size ({human(opts.max_bytes)})")
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
            )

    if opts.dry_run:
        return Result(file=file, state="skipped", path=str(final), verified=None)

    resumed_from = part.stat().st_size if part.exists() else 0
    progress = opts.progress
    if progress:
        progress.start(name, file.size)
    try:
        return _attempt(client, file, part, meta_path, final, opts, resumed_from)
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
                if exc.status == 0 and "exceeds --max-size" in str(exc):
                    # Do not leave an over-limit part for the next run to
                    # resume straight back into the same wall.
                    _drop(part, meta_path)
                    return Result(file=file, state="failed", error=str(exc))
                if exc.status == 403 and exc.transient and blocked < client.block_retries:
                    blocked += 1
                elif exc.status in (401, 403, 404, 410):
                    break  # terminal for this URL; a fallback may still work
            except (OSError, ConnectionError) as exc:
                last = exc
            if attempt < opts.retries:
                time.sleep(min(2**attempt, 10) * (0.7 + random.random() * 0.6))
                resumed_from = part.stat().st_size if part.exists() else 0
        if done:
            break
        if index < len(candidates) - 1:
            _drop(part, meta_path)
            resumed_from = 0
            continue
        _drop(part, meta_path)
        return Result(file=file, state="failed", error=str(last))

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
            return Result(file=file, state="failed", error=f"re-download failed: {exc}")
        if verdict is False:
            _drop(part, meta_path)
            return Result(file=file, state="failed", error="checksum mismatch")

    os.replace(part, final)
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
        _enforce_total(record, opts.max_bytes)

    taken: set[str] = set()
    lock = threading.Lock()
    results: list[Result] = []

    if opts.concurrency <= 1:
        for file in files:
            results.append(fetch_file(client, file, opts, taken))
        return results

    with ThreadPoolExecutor(max_workers=opts.concurrency) as pool:
        futures = {}
        for file in files:
            with lock:
                futures[pool.submit(fetch_file, client, file, opts, taken)] = file
        for future in as_completed(futures):
            results.append(future.result())
    order = {id(f): i for i, f in enumerate(record.files)}
    results.sort(key=lambda r: order.get(id(r.file), 0))
    return results


def _enforce_total(record: Record, limit: int) -> None:
    total = record.total_bytes
    if total is None:
        return
    if total > limit:
        from .errors import SourceError

        raise SourceError(f"record is {human(total)}, over --max-size ({human(limit)})")

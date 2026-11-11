"""Command line interface.

Exit codes are part of the contract: 0 all good, 1 everything failed or the
target was unusable, 2 partial success, 130 interrupted (partials are kept).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import NoReturn
from urllib.parse import urlsplit

from . import auth, consts, debug, manifest, naming, urls, verify
from . import remote as remote_check
from .client import Client
from .downloader import Options, check_disk, check_total, fetch_record, human
from .downloader import matches as downloader_matches
from .errors import AcFError, PartialError, TargetError
from .progress import Progress
from .sources import doi, registry, removed
from .verify import reason as verify_reason

EXIT_OK, EXIT_FAIL, EXIT_PARTIAL, EXIT_USAGE, EXIT_INTERRUPT = 0, 1, 2, 64, 130
#: Bumped only when a field changes meaning or disappears. New fields do not.
#: 2: file rows carry ``selected``; ``state`` may be ``not-selected``/``planned``.
REPORT_SCHEMA = 2
EXAMPLES = {
    "zenodo": "zenodo:123456",
    "arxiv": "arxiv:2401.12345",
    "doi": "10.5281/zenodo.123456",
    "direct": "https://ndownloader.figshare.com/files/70600233/report.pdf",
}
EPILOG = """target forms:
  10.5281/zenodo.123456        a DOI
  zenodo:123456                a record id
  arxiv:2401.12345
  https://host/file.tar.gz     any URL

exit codes:
  0 ok    1 failed    2 partial    64 bad usage    130 interrupted (partials kept, rerun to resume)

environment: ZENODO_TOKEN (sent only to Zenodo)
"""
_SIZE = re.compile(r"^(\d+(?:\.\d+)?)\s*([KMGT]i?B?|B?)$", re.I)


def parse_size(text: str) -> int:
    hit = _SIZE.match(text.strip())
    if not hit:
        raise argparse.ArgumentTypeError(f"bad size: {text!r}")
    value = float(hit.group(1))
    unit = (hit.group(2) or "").upper().rstrip("B")
    scale = {
        "": 1,
        "K": 1024,
        "KI": 1024,
        "M": 1 << 20,
        "MI": 1 << 20,
        "G": 1 << 30,
        "GI": 1 << 30,
        "T": 1 << 40,
        "TI": 1 << 40,
    }[unit]
    return int(value * scale)


def _rate(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number: {text!r}") from None
    if value <= 0:
        raise argparse.ArgumentTypeError("must be > 0; 2 means two requests/second")
    return value


def _positive(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a whole number: {text!r}") from None
    if value < 1:
        raise argparse.ArgumentTypeError("must be >= 1")
    return value


def _count(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a whole number: {text!r}") from None
    if value < 0:
        raise argparse.ArgumentTypeError(f"cannot retry {value} times")
    return value


def _timeout(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number of seconds: {text!r}") from None
    if value <= 0:
        raise argparse.ArgumentTypeError("must be > 0 seconds")
    return value


def _proxy(text: str) -> str:
    candidate = text if "://" in text else f"http://{text}"
    parts = urlsplit(candidate)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise argparse.ArgumentTypeError(
            f"expected http://host:port or https://host:port, got {text!r}"
        )
    return candidate


def _ca_bundle(text: str) -> str:
    if not Path(text).is_file():
        raise argparse.ArgumentTypeError(f"no such file: {text!r}")
    return text


def _checksum(text: str) -> str:
    """Validate ``--checksum`` before any request is made."""
    parsed = verify.parse(text)
    if parsed is None:
        raise argparse.ArgumentTypeError(f"expected md5:<hex> or sha256:<hex>, got {text!r}")
    algo, value = parsed
    if algo not in ("md5", "sha256"):
        raise argparse.ArgumentTypeError(f"only md5: and sha256: are accepted, got {algo}:")
    return f"{algo}:{value}"


def cmd_sources(_: argparse.Namespace) -> int:
    for name in registry.names():
        print(f"{name:<9} {EXAMPLES.get(name, '')}".rstrip())
    print(removed.SOURCES_NOTE)
    return EXIT_OK


def _client(args: argparse.Namespace) -> Client:
    tokens = {} if getattr(args, "no_token", False) else auth.load()
    args.tokens = tokens
    return Client(
        rate=args.rate,
        retries=args.retries,
        block_retries=args.retry_403,
        allow_http=args.allow_http,
        timeout=args.timeout,
        proxy={"http": args.proxy, "https": args.proxy} if args.proxy else None,
        no_proxy=args.no_proxy,
        ca_bundle=args.ca_bundle,
        headers=_headers(args.header),
        auth={source: token.headers for source, token in tokens.items()},
    )


def _headers(raw: list[str] | None) -> dict[str, str]:
    out = {}
    for item in raw or []:
        key, sep, value = item.partition(":")
        if not sep:
            raise TargetError(f"--header needs 'Name: value', got {item!r}")
        out[key.strip()] = value.strip()
    return out


def _looks_like_target(text: str) -> bool:
    if text.isdigit() or doi.match(text):
        return True
    return any(adapter.match(text) for adapter in registry.ADAPTERS)


def _recover_target(args: argparse.Namespace) -> None:
    """``--only a.pdf <target>`` makes argparse eat the target; put it back.

    Checked only when the target is missing, so a file that happens to look
    like an id keeps working as long as the target is spelled out.
    """
    if getattr(args, "target", None) is not None or not getattr(args, "only", None):
        return
    if len(args.only) > 1 and _looks_like_target(args.only[-1]):
        args.target = args.only.pop()


def _resolve(args: argparse.Namespace, client: Client):
    record = registry.resolve(client, args.target)
    return record


def _token_for(args: argparse.Namespace, record):  # noqa: ANN001, ANN202
    return getattr(args, "tokens", {}).get(record.source)


def cmd_manifest(args: argparse.Namespace) -> int:
    client = _client(args)
    record = _resolve(args, client)
    data = manifest.build(
        record, [], _token_for(args, record), planned=True, redact=not args.record_full_url
    )
    out = Path(args.out or "manifest.json")
    manifest.write(out, data)
    print(f"{len(record.files)} file(s) -> {out}")
    _print_header(record, lambda value: _hide(value, args))
    if token := _token_for(args, record):
        print(f"auth    : token from {token}")
    return EXIT_OK


def _report(args: argparse.Namespace) -> dict:
    """The envelope every ``--json`` document starts from."""
    return {
        "tool": {"name": "acadfetch", "version": consts.__version__},
        "report": REPORT_SCHEMA,
        "command": getattr(args, "command", ""),
        "started_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "error": None,
    }


def _hide(text: str, args: argparse.Namespace) -> str:
    """Redact credential query parameters unless --record-full-url asked for
    the original string."""
    return text if getattr(args, "record_full_url", False) else urls.redact(text)


def _hide_text(text: str, args: argparse.Namespace) -> str:
    return text if getattr(args, "record_full_url", False) else urls.redact_text(text)


def _file_row(row, redact: bool = True) -> dict:  # noqa: ANN001
    hide = urls.redact if redact else (lambda value: value)
    return {
        "name": naming.sanitize(row.name or row.file.name),
        "size": row.file.size,
        "bytes": row.bytes,
        "state": row.state,
        "selected": True,
        "http_status": row.http_status,
        "final_url": hide(row.final_url) if row.final_url else None,
        "verified": row.verified,
        "verified_reason": verify_reason(row.file.checksum, row.verified),
        "resumed_from": row.resumed_from,
        "part": Path(row.part).name if row.part else None,
        "error": row.error,
    }


def _emit_error(args: argparse.Namespace, message: str) -> None:
    """A ``--json`` caller gets a document even when the run died early."""
    if not getattr(args, "json", False):
        return
    doc = _report(args)
    doc["error"] = message
    _emit(doc)


def _emit(doc: dict) -> None:
    # sorts nothing: the order above is the order a reader wants.
    print(json.dumps(doc, ensure_ascii=False))


def _print_header(record, hide=urls.redact, stream=None) -> None:  # noqa: ANN001
    out = sys.stdout if stream is None else stream
    print(f"title   : {record.title or '(untitled)'}", file=out)
    if record.doi:
        print(f"doi     : {record.doi}", file=out)
    print(f"license : {record.license or 'UNKNOWN - check before redistributing'}", file=out)
    total = record.total_bytes
    print(
        f"files   : {len(record.files)}" + (f", {human(total)}" if total is not None else ""),
        file=out,
    )
    if record.landing:
        print(f"landing : {hide(record.landing)}", file=out)


def _write_urls(files, out_dir, target) -> None:  # noqa: ANN001
    lines = []
    for file in files:
        lines.append(file.url)
        lines.append(f"  out={naming.sanitize(file.name)}")
        lines.append(f"  dir={out_dir}")
    text = "\n".join(lines) + "\n"
    if target == "-":
        sys.stdout.write(text)
        return
    Path(target).write_text(text, "utf-8")
    print(f"{len(files)} url(s) -> {target}", file=sys.stderr)


def _no_match(record, args) -> int:  # noqa: ANN001
    """A filter that matched nothing is a failure that can name the candidates."""
    available = ", ".join(naming.sanitize(f.name) for f in record.files[:8]) or "(none)"
    wanted = " ".join(
        part
        for part in (
            f"--only {', '.join(args.only)}" if args.only else "",
            f"--glob {', '.join(args.globs)}" if args.globs else "",
        )
        if part
    )
    message = f"no file matched {wanted}; available: {available}"
    print(f"acadfetch: {message}", file=sys.stderr)
    _emit_error(args, message)
    return EXIT_FAIL


def _selected(record, args):  # noqa: ANN001, ANN202
    if not args.only and not args.globs:
        return record.files
    return [f for f in record.files if downloader_matches(f, args.only, args.globs)]


def cmd_fetch(args: argparse.Namespace) -> int:
    started = time.monotonic()
    client = _client(args)
    record = _resolve(args, client)
    if args.checksum:
        # Explicit wins over whatever the source published, and over nothing.
        record = replace(
            record,
            files=[replace(f, checksum=args.checksum, checksum_source="cli") for f in record.files],
        )
    doc = _report(args)
    doc.update(
        target=_hide(record.target, args),
        source=record.source,
        doi=record.doi,
        license=record.license,
        landing=_hide(record.landing, args),
    )
    # A URL list on stdout must stay machine-readable, so the header and the
    # token line move to stderr for --urls.
    header = sys.stderr if args.urls else sys.stdout
    if not args.json:
        _print_header(record, lambda value: _hide(value, args), stream=header)
    token = _token_for(args, record)
    if token and not args.json:
        print(f"auth    : token from {token}", file=header)

    out_dir = Path(args.out or f"acadfetch-out/{record.source}-{_slug(record)}")
    doc["out"] = str(out_dir)
    doc["dry_run"] = bool(args.dry_run)

    if args.urls:
        if args.json:
            raise TargetError("--urls writes a text list; drop --json")
        chosen = _selected(record, args)
        if not chosen:
            return _no_match(record, args)
        _write_urls(chosen, out_dir, args.urls)
        return EXIT_OK
    # Say whether a credential was stripped, so a script can tell the manifest
    # is not replayable as-is.
    doc["url_redacted"] = record.target != _hide(record.target, args) or record.landing != _hide(
        record.landing, args
    )
    # Progress shares stdout with the report, so it moves to stderr under --json.
    progress = Progress(stream=sys.stderr if args.json else None, enabled=not args.quiet)
    opts = Options(
        out=out_dir,
        concurrency=args.concurrency,
        retries=args.retries,
        dry_run=args.dry_run,
        max_bytes=args.max_size,
        progress=progress,
        fsync=not args.no_fsync,
        force=args.force,
        explicit_out=bool(args.out),
    )

    if args.dry_run:
        chosen = _selected(record, args)
        if not chosen:
            return _no_match(record, args)
        if args.max_size:
            check_total(replace(record, files=chosen), args.max_size)
        try:
            # A plan reports the shortfall; it is not yet a failure.
            free, needed = check_disk(chosen, out_dir, strict=False)
        except OSError:  # a filesystem that will not say; not worth failing for
            free = needed = 0
        disk = {"free": free, "needed": needed, "ok": not needed or free >= needed}
        if args.json:
            doc["files"] = [
                {
                    "name": naming.sanitize(f.name),
                    "size": f.size,
                    "bytes": 0,
                    "state": "planned",
                    "selected": True,
                    "verified": None,
                    "verified_reason": verify_reason(f.checksum, None),
                    "resumed_from": 0,
                    "part": None,
                    "error": "",
                }
                for f in chosen
            ]
            doc["disk"] = disk
            doc["summary"] = {"total": len(chosen), "downloaded": 0, "skipped": 0, "failed": 0}
            doc["elapsed_s"] = round(time.monotonic() - started, 3)
            _emit(doc)
            return EXIT_OK
        print(f"would write to {out_dir}")
        if needed:
            print(
                f"disk    : {human(needed)} needed, {human(free)} free"
                + ("" if disk["ok"] else "  NOT ENOUGH")
            )
        for file in chosen:
            print(f"  would fetch {naming.sanitize(file.name)} ({human(file.size)})")
        return EXIT_OK

    try:
        results = fetch_record(record, opts, client, only=args.only, globs=args.globs)
    except PartialError:
        return _no_match(record, args)
    finally:
        progress.finish()

    failures = [r for r in results if not r.ok]
    doc["files"] = [_file_row(r, redact=not args.record_full_url) for r in results]
    doc["summary"] = {
        "total": len(results),
        "downloaded": sum(1 for r in results if r.state == "downloaded"),
        "skipped": sum(1 for r in results if r.state == "skipped"),
        "failed": len(failures),
    }
    doc["elapsed_s"] = round(time.monotonic() - started, 3)
    for row in results if not args.json else []:
        mark = {"downloaded": "ok  ", "skipped": "skip", "failed": "FAIL"}[row.state]
        extra = ""
        if row.resumed_from:
            extra = f" (resumed at {human(row.resumed_from)})"
        if row.verified is False:
            extra += " [CHECKSUM MISMATCH]"
        print(f"{mark} {naming.sanitize(row.file.name)} {human(row.bytes)}{extra}")
        if row.error:
            print(f"     {row.error}")
        if row.part:
            # The bytes stay on disk, so say so: otherwise a failure reads as
            # "nothing happened" and the rerun looks like it starts over.
            print(f"     kept {human(row.bytes)} in {Path(row.part).name} - rerun to resume")

    data = manifest.build(
        record,
        results,
        token,
        # Which files this run asked for: a --only run must not look like a
        # run that fetched everything and then lost most of it.
        selected_ids={id(f) for f in _selected(record, args)},
        redact=not args.record_full_url,
    )
    code = EXIT_OK
    try:
        manifest.write(out_dir / "manifest.json", data)
    except OSError as exc:
        # The bytes may all be on disk; losing the manifest is a partial
        # outcome, not a crash.
        print(f"acadfetch: could not write the manifest: {exc}", file=sys.stderr)
        doc["error"] = f"could not write the manifest: {exc}"
        code = EXIT_PARTIAL if len(results) > len(failures) else EXIT_FAIL
    else:
        if not args.json:
            print(f"{len(results) - len(failures)}/{len(results)} file(s) in {out_dir}")
        if args.manifest_out:
            try:
                manifest.write(Path(args.manifest_out), data)
            except OSError as exc:
                print(f"acadfetch: could not write {args.manifest_out}: {exc}", file=sys.stderr)
                doc["error"] = f"could not write {args.manifest_out}: {exc}"
                code = EXIT_PARTIAL

    if opts.interrupted:
        # The manifest above covers whatever finished, which is the difference
        # between "a partial run you can reason about" and a bare exit 130.
        print("interrupted - partial files kept, rerun to resume", file=sys.stderr)
        code = EXIT_INTERRUPT
    elif failures and len(failures) == len(results):
        code = EXIT_FAIL
    elif failures:
        code = EXIT_PARTIAL
    doc["summary"]["ok"] = code == EXIT_OK
    if args.json:
        _emit(doc)
    return code


def cmd_verify(args: argparse.Namespace) -> int:
    doc = _report(args)
    doc["manifest"] = args.manifest
    manifest_path = Path(args.manifest)
    # Names in a manifest are relative to the directory it was written into.
    # Defaulting to the cwd made the README's own example fail.
    root = Path(args.root) if args.root else manifest_path.parent
    if manifest.peek_schema(manifest_path) == 1:
        print(manifest.SCHEMA_1_NOTE, file=sys.stderr)
    doc["root"] = str(root)
    data = manifest.load(manifest_path)
    rows = manifest.verify_manifest(manifest_path, root, allow_escape=args.allow_escape, data=data)
    remote: dict[str, tuple[str, str]] = {}
    if args.against_remote:
        published_now, reason = remote_check.published(_client(args), data)
        entries = [e for e in data.get("files") or [] if isinstance(e, dict)]
        # verify_manifest walks the same list in order; the rows do not carry
        # the URL or checksum themselves.
        remote = {
            row["name"]: remote_check.judge(
                None if not published_now and reason else published_now, entry, reason
            )
            for row, entry in zip(rows, entries, strict=False)
        }
    skipped = sum(1 for row in rows if row["status"] == "skipped")
    hard = sum(1 for row in rows if row["status"] not in ("ok", "skipped"))
    # --require-all turns "you never asked for it" into a failure; the plain
    # default does not, because a --only manifest is a complete description of
    # the files it did fetch.
    bad = hard + (skipped if args.require_all else 0)
    ok = len(rows) - hard - skipped
    if not args.json:
        for row in rows:
            status, detail = remote.get(row["name"], ("", ""))
            tail = f"  remote: {status}" if status else ""
            if detail:
                tail += f" ({detail})"
            print(f"{row['status']:>9} {row['name']}{tail}")
        if rows:
            print()
            tally = f"{ok}/{len(rows)} ok"
            if skipped:
                tally += f", {skipped} skipped"
            print(tally)
    if not args.allow_escape and any(row["status"] == "escaped" for row in rows):
        print(f"some names leave {root} - --allow-escape follows them", file=sys.stderr)
    remote_bad = 0
    if remote:
        total = remote_check.counts([{"remote": status} for status, _ in remote.values()])
        if total[remote_check.MISMATCH]:
            remote_bad = total[remote_check.MISMATCH]
        if args.require_remote and total[remote_check.UNAVAILABLE]:
            remote_bad += total[remote_check.UNAVAILABLE]
        bad += remote_bad
        data["remote_checked_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        try:
            manifest.write(manifest_path, data)
        except OSError as exc:
            print(f"acadfetch: could not record remote_checked_at: {exc}", file=sys.stderr)
        doc["remote_checked_at"] = data["remote_checked_at"]
        doc["remote"] = total
        if total[remote_check.MISMATCH]:
            print(
                f"{total[remote_check.MISMATCH]} file(s) no longer match what the source "
                f"publishes now",
                file=sys.stderr,
            )
        if total[remote_check.UNAVAILABLE] and not args.require_remote:
            print(
                f"{total[remote_check.UNAVAILABLE]} file(s) could not be re-checked; "
                f"--require-remote counts them",
                file=sys.stderr,
            )
    doc["rows"] = [
        {
            **{
                k: row[k]
                for k in ("name", "status", "state", "sha256", "expected", "actual")
                if k in row
            },
            **(
                {"remote": remote[row["name"]][0], "remote_detail": remote[row["name"]][1]}
                if row["name"] in remote
                else {}
            ),
        }
        for row in rows
    ]
    doc["summary"] = {
        "total": len(rows),
        "ok": ok,
        "bad": bad,
        "skipped": skipped,
        "ok_run": bool(rows) and not bad,
    }
    if not rows:
        print("the manifest lists no files", file=sys.stderr)
        doc["error"] = "the manifest lists no files"
    elif skipped and not args.require_all:
        print(
            f"{skipped} file(s) were not selected (-n/--only); --require-all counts them",
            file=sys.stderr,
        )
    if args.json:
        _emit(doc)
    if not rows:
        return EXIT_FAIL
    return EXIT_FAIL if bad else EXIT_OK


def _slug(record) -> str:  # noqa: ANN001
    """A directory name that identifies the target, not only its filename.

    ``data.csv`` from two hosts is two different datasets; the host and a short
    hash of the full URL keep them apart. A DOI is already unique.
    """
    if record.doi:
        return naming.sanitize(record.doi.replace("/", "_"))
    parts = urlsplit(record.target)
    path = parts.path or record.target
    name = naming.sanitize(path.rsplit("/", 1)[-1] or "record")
    host = naming.sanitize(parts.hostname or "host")
    digest = hashlib.sha256(record.target.encode("utf-8", "replace")).hexdigest()[:8]
    return f"{host}-{digest}-{name}"


class Parser(argparse.ArgumentParser):
    """Usage errors are not "partial": exit 64 keeps 2 meaning a partial run."""

    def error(self, message: str) -> NoReturn:  # noqa: D102
        self.print_usage(sys.stderr)
        self.exit(EXIT_USAGE, f"{self.prog}: error: {message}\n")


def build_parser() -> argparse.ArgumentParser:
    parser = Parser(
        prog="acadfetch",
        description="Resumable, rate-limited, checksum-verified bulk downloads from data repositories.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"acadfetch {consts.__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def network_flags(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--rate",
            type=_rate,
            default=consts.DEFAULT_RATE,
            help="requests per second, global (default: %(default)s)",
        )
        p.add_argument(
            "--retries",
            type=_count,
            default=consts.DEFAULT_RETRIES,
            help="retries per request on 5xx/429/network errors (default: %(default)s)",
        )
        p.add_argument(
            "--retry-403",
            type=_count,
            default=consts.WAF_403_RETRIES,
            metavar="N",
            help="retries for a WAF block page, i.e. a 403 with an HTML body; 0 makes "
            "every 403 final (default: %(default)s)",
        )
        p.add_argument(
            "--header",
            action="append",
            metavar="NAME:VALUE",
            help="extra request header, repeatable (e.g. 'Authorization: Bearer <token>')",
        )
        p.add_argument(
            "--verbose",
            action="store_true",
            help="trace requests and retries to stderr, with tracebacks (also ACADFETCH_DEBUG=1)",
        )
        p.add_argument(
            "--timeout",
            type=_timeout,
            default=consts.DEFAULT_TIMEOUT,
            metavar="SECONDS",
            help="per-request timeout, connect and idle (default: %(default)s)",
        )
        p.add_argument(
            "--proxy",
            type=_proxy,
            metavar="URL",
            help="http:// or https:// proxy for every request; overrides the "
            "environment and wins over --no-proxy",
        )
        p.add_argument(
            "--no-proxy",
            action="store_true",
            help="ignore HTTP_PROXY / HTTPS_PROXY / ALL_PROXY",
        )
        p.add_argument(
            "--ca-bundle",
            type=_ca_bundle,
            metavar="PATH",
            help="PEM file to trust instead of the system store (corporate TLS inspection)",
        )
        p.add_argument(
            "--allow-http",
            action="store_true",
            help="keep Authorization on an https -> http redirect hop",
        )
        p.add_argument(
            "--no-token",
            action="store_true",
            help="ignore $ZENODO_TOKEN",
        )

    fetch = sub.add_parser(
        "fetch",
        help="download every file of a record",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    fetch.add_argument(
        "target",
        nargs="?",
        help="DOI, zenodo:<id>, arxiv:<id>, or URL",
    )
    fetch.add_argument(
        "-o", "--out", help="output directory (default: acadfetch-out/<source>-<id>)"
    )
    fetch.add_argument(
        "-j",
        "--concurrency",
        type=_positive,
        default=consts.DEFAULT_CONCURRENCY,
        help="parallel transfers (default: %(default)s)",
    )
    fetch.add_argument("--max-size", type=parse_size, help="abort above this total, e.g. 500MB")
    fetch.add_argument(
        "--checksum",
        type=_checksum,
        metavar="ALGO:HEX",
        help="verify against this checksum instead (md5:<hex> or sha256:<hex>), "
        "for sources that publish none; required when migrating a figshare target",
    )
    fetch.add_argument(
        "--only",
        nargs="+",
        metavar="NAME",
        help="fetch only these file names",
    )
    fetch.add_argument(
        "--glob",
        nargs="+",
        dest="globs",
        metavar="PATTERN",
        help="fetch only files whose name matches a shell pattern, e.g. '*.pdf'",
    )
    fetch.add_argument(
        "-w",
        "--urls",
        metavar="PATH",
        help="write an aria2-compatible URL list instead of downloading; '-' is stdout",
    )
    fetch.add_argument("--manifest-out", help="also write the manifest here")
    fetch.add_argument("-n", "--dry-run", action="store_true", help="list what would be fetched")
    fetch.add_argument("-q", "--quiet", action="store_true", help="no progress line")
    fetch.add_argument(
        "--force",
        action="store_true",
        help="fetch again even when a matching file is already there",
    )
    fetch.add_argument(
        "--no-fsync",
        action="store_true",
        help="skip the flush before each rename (faster, weaker against a power cut)",
    )
    fetch.add_argument(
        "--json",
        action="store_true",
        help="print one JSON report to stdout, progress to stderr",
    )
    fetch.add_argument(
        "--record-full-url",
        action="store_true",
        help="keep credential query parameters (X-Amz-Signature, token, …) in "
        "the manifest and on stdout instead of stripping them",
    )
    network_flags(fetch)
    fetch.set_defaults(func=cmd_fetch)

    man = sub.add_parser("manifest", help="write a manifest without downloading")
    man.add_argument("target")
    man.add_argument("-o", "--out")
    man.add_argument(
        "--record-full-url",
        action="store_true",
        help="keep credential query parameters in the manifest",
    )
    network_flags(man)
    man.set_defaults(func=cmd_manifest)

    ver = sub.add_parser("verify", help="re-check a local directory against its manifest")
    ver.add_argument("manifest")
    ver.add_argument(
        "--root",
        help="directory the manifest's names are relative to (default: the manifest's own directory)",
    )
    ver.add_argument(
        "--allow-escape",
        action="store_true",
        help="follow names that leave --root (a manifest can point outside it)",
    )
    ver.add_argument(
        "--require-all",
        action="store_true",
        help="count a file that was never selected (--only, or a manifest-only "
        "run) as a failure instead of skipping it",
    )
    ver.add_argument(
        "--against-remote",
        action="store_true",
        help="ask the source again and compare what it publishes now (network)",
    )
    ver.add_argument(
        "--require-remote",
        action="store_true",
        help="count a file that could not be re-checked as a failure",
    )
    ver.add_argument(
        "--json",
        action="store_true",
        help="print one JSON report to stdout instead of rows",
    )
    network_flags(ver)
    ver.set_defaults(func=cmd_verify)

    src = sub.add_parser("sources", help="list supported sources")
    src.set_defaults(func=cmd_sources)
    return parser


def main(argv: list[str] | None = None) -> int:
    # Repository titles are full of non-ASCII; a cp936/cp1252 console would
    # otherwise abort or mangle them.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    debug.configure(getattr(args, "verbose", False))
    _recover_target(args)
    if getattr(args, "command", "") == "fetch" and getattr(args, "target", None) is None:
        message = (
            "which record? try one of:\n"
            "  acadfetch fetch 10.5281/zenodo.23612969\n"
            "  acadfetch fetch arxiv:1706.03762\n"
            "  acadfetch fetch https://ndownloader.figshare.com/files/70600233/report.pdf"
        )
        print(f"acadfetch: {message}", file=sys.stderr)
        _emit_error(args, message)
        return EXIT_FAIL
    try:
        return args.func(args)
    except AcFError as exc:  # TargetError is one
        debug.log_exception(exc)
        # An error sentence carries the URL, and that URL may carry a signature.
        message = _hide_text(str(exc), args)
        print(f"acadfetch: {message}", file=sys.stderr)
        _emit_error(args, message)
        return EXIT_FAIL
    except OSError as exc:  # a filesystem surprise, not a bug report
        debug.log_exception(exc)
        message = f"{exc.strerror or exc}" if exc.filename else str(exc)
        print(f"acadfetch: {message}", file=sys.stderr)
        _emit_error(args, message)
        return EXIT_FAIL
    except KeyboardInterrupt:
        print("\ninterrupted - partial files kept, rerun to resume", file=sys.stderr)
        return EXIT_INTERRUPT


if __name__ == "__main__":
    sys.exit(main())

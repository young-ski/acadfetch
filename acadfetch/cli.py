"""Command line interface.

Exit codes are part of the contract: 0 all good, 1 everything failed or the
target was unusable, 2 partial success, 130 interrupted (partials are kept).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from . import auth, consts, manifest, naming
from .client import Client
from .downloader import Options, check_disk, check_total, fetch_record, human
from .errors import AcFError, PartialError, TargetError
from .progress import Progress
from .sources import doi, registry
from .verify import reason as verify_reason

EXIT_OK, EXIT_FAIL, EXIT_PARTIAL, EXIT_INTERRUPT = 0, 1, 2, 130
#: Bumped only when a field changes meaning or disappears. New fields do not.
REPORT_SCHEMA = 1
EXAMPLES = {
    "zenodo": "zenodo:123456",
    "figshare": "figshare:987654",
    "arxiv": "arxiv:2401.12345",
    "doi": "10.5281/zenodo.123456",
    "direct": "https://example.org/data.tar.gz",
}
EPILOG = """target forms:
  10.5281/zenodo.123456        a DOI
  zenodo:123456                a record id
  figshare:987654
  arxiv:2401.12345
  https://host/file.tar.gz     any URL

exit codes:
  0 ok    1 failed    2 partial    130 interrupted (partials kept, rerun to resume)

environment: ZENODO_TOKEN, FIGSHARE_TOKEN (sent only to that repository)
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


def cmd_sources(_: argparse.Namespace) -> int:
    for name in registry.names():
        print(f"{name:<9} {EXAMPLES.get(name, '')}".rstrip())
    return EXIT_OK


def _client(args: argparse.Namespace) -> Client:
    tokens = {} if getattr(args, "no_token", False) else auth.load()
    args.tokens = tokens
    return Client(
        rate=args.rate,
        retries=args.retries,
        block_retries=args.retry_403,
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
    data = manifest.build(record, [], _token_for(args, record))
    out = Path(args.out or "manifest.json")
    manifest.write(out, data)
    print(f"{len(record.files)} file(s) -> {out}")
    _print_header(record)
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


def _file_row(row) -> dict:  # noqa: ANN001
    return {
        "name": naming.sanitize(row.name or row.file.name),
        "size": row.file.size,
        "bytes": row.bytes,
        "state": row.state,
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


def _print_header(record) -> None:  # noqa: ANN001
    print(f"title   : {record.title or '(untitled)'}")
    if record.doi:
        print(f"doi     : {record.doi}")
    print(f"license : {record.license or 'UNKNOWN - check before redistributing'}")
    total = record.total_bytes
    print(f"files   : {len(record.files)}" + (f", {human(total)}" if total is not None else ""))
    if record.landing:
        print(f"landing : {record.landing}")


def _selected(record, only: list[str] | None):  # noqa: ANN001, ANN202
    if not only:
        return record.files
    wanted = {n.lower() for n in only}
    return [
        f
        for f in record.files
        if naming.sanitize(f.name).lower() in wanted or f.name.lower() in wanted
    ]


def cmd_fetch(args: argparse.Namespace) -> int:
    started = time.monotonic()
    client = _client(args)
    record = _resolve(args, client)
    doc = _report(args)
    doc.update(
        target=record.target,
        source=record.source,
        doi=record.doi,
        license=record.license,
        landing=record.landing,
    )
    if not args.json:
        _print_header(record)
    token = _token_for(args, record)
    if token and not args.json:
        print(f"auth    : token from {token}")

    out_dir = Path(args.out or f"acadfetch-out/{record.source}-{_slug(record)}")
    doc["out"] = str(out_dir)
    doc["dry_run"] = bool(args.dry_run)
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
    )

    if args.dry_run:
        chosen = _selected(record, args.only)
        if not chosen:
            print(f"acadfetch: no file matched --only {', '.join(args.only)}", file=sys.stderr)
            return EXIT_FAIL
        if args.max_size:
            check_total(replace(record, files=chosen), args.max_size)
        try:
            free, needed = check_disk(chosen, out_dir)
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
        results = fetch_record(record, opts, client, only=args.only)
    except PartialError:
        print(f"acadfetch: no file matched --only {', '.join(args.only)}", file=sys.stderr)
        return EXIT_FAIL
    finally:
        progress.finish()

    failures = [r for r in results if not r.ok]
    doc["files"] = [_file_row(r) for r in results]
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

    data = manifest.build(record, results, token)
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
    rows = manifest.verify_manifest(manifest_path, root, allow_escape=args.allow_escape)
    bad = sum(1 for row in rows if row["status"] != "ok")
    if not args.json:
        for row in rows:
            print(f"{row['status']:>9} {row['name']}")
        print()
        print(f"{len(rows) - bad}/{len(rows)} ok")
    if not args.allow_escape and any(row["status"] == "escaped" for row in rows):
        print(f"some names leave {root} - --allow-escape follows them", file=sys.stderr)
    doc["rows"] = [
        {k: row[k] for k in ("name", "status", "expected", "actual") if k in row} for row in rows
    ]
    doc["summary"] = {"total": len(rows), "ok": len(rows) - bad, "bad": bad, "ok_run": not bad}
    if not rows:
        print("the manifest lists no files", file=sys.stderr)
        doc["error"] = "the manifest lists no files"
    if args.json:
        _emit(doc)
    if not rows:
        return EXIT_FAIL
    return EXIT_FAIL if bad else EXIT_OK


def _slug(record) -> str:  # noqa: ANN001
    if record.doi:
        return naming.sanitize(record.doi.replace("/", "_"))
    return naming.sanitize(record.target.rsplit("/", 1)[-1] or "record")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
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
            "--no-token",
            action="store_true",
            help="ignore $ZENODO_TOKEN / $FIGSHARE_TOKEN",
        )

    fetch = sub.add_parser("fetch", help="download every file of a record")
    fetch.add_argument(
        "target",
        nargs="?",
        help="DOI, zenodo:<id>, figshare:<id>, arxiv:<id>, or URL",
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
        "--only",
        nargs="+",
        metavar="NAME",
        help="fetch only these file names; put the target first, or the names swallow it",
    )
    fetch.add_argument("--manifest-out", help="also write the manifest here")
    fetch.add_argument("-n", "--dry-run", action="store_true", help="list what would be fetched")
    fetch.add_argument("-q", "--quiet", action="store_true", help="no progress line")
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
    network_flags(fetch)
    fetch.set_defaults(func=cmd_fetch)

    man = sub.add_parser("manifest", help="write a manifest without downloading")
    man.add_argument("target")
    man.add_argument("-o", "--out")
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
        "--json",
        action="store_true",
        help="print one JSON report to stdout instead of rows",
    )
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
    _recover_target(args)
    if getattr(args, "command", "") == "fetch" and getattr(args, "target", None) is None:
        message = (
            "which record? put a target after --only, e.g. "
            "acadfetch fetch zenodo:123456 --only raw.tar.gz"
        )
        print(f"acadfetch: {message}", file=sys.stderr)
        _emit_error(args, message)
        return EXIT_FAIL
    try:
        return args.func(args)
    except AcFError as exc:  # TargetError is one
        print(f"acadfetch: {exc}", file=sys.stderr)
        _emit_error(args, str(exc))
        return EXIT_FAIL
    except KeyboardInterrupt:
        print("\ninterrupted - partial files kept, rerun to resume", file=sys.stderr)
        return EXIT_INTERRUPT


if __name__ == "__main__":
    sys.exit(main())

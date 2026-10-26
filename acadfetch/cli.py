"""Command line interface.

Exit codes are part of the contract: 0 all good, 1 everything failed or the
target was unusable, 2 partial success, 130 interrupted (partials are kept).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from . import auth, consts, manifest, naming
from .client import Client
from .downloader import Options, fetch_record, human
from .errors import AcFError, PartialError, TargetError
from .progress import Progress
from .sources import registry

EXIT_OK, EXIT_FAIL, EXIT_PARTIAL, EXIT_INTERRUPT = 0, 1, 2, 130
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
        print(name)
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


def _print_header(record) -> None:  # noqa: ANN001
    print(f"title   : {record.title or '(untitled)'}")
    if record.doi:
        print(f"doi     : {record.doi}")
    print(f"license : {record.license or 'UNKNOWN - check before redistributing'}")
    total = record.total_bytes
    print(f"files   : {len(record.files)}" + (f", {human(total)}" if total is not None else ""))
    if record.landing:
        print(f"landing : {record.landing}")


def cmd_fetch(args: argparse.Namespace) -> int:
    client = _client(args)
    record = _resolve(args, client)
    _print_header(record)
    token = _token_for(args, record)
    if token:
        print(f"auth    : token from {token}")

    out_dir = Path(args.out or f"acadfetch-out/{record.source}-{_slug(record)}")
    progress = Progress(enabled=not args.quiet)
    opts = Options(
        out=out_dir,
        concurrency=args.concurrency,
        retries=args.retries,
        dry_run=args.dry_run,
        max_bytes=args.max_size,
        progress=progress,
    )

    if args.dry_run:
        for file in record.files:
            print(f"  would fetch {naming.sanitize(file.name)} ({human(file.size)})")
        return EXIT_OK

    try:
        results = fetch_record(record, opts, client, only=args.only)
    except PartialError:
        print(f"no file matched --only {args.only}", file=sys.stderr)
        return EXIT_FAIL
    except KeyboardInterrupt:
        progress.finish()
        raise
    finally:
        progress.finish()

    failures = [r for r in results if not r.ok]
    for row in results:
        mark = {"downloaded": "ok  ", "skipped": "skip", "failed": "FAIL"}[row.state]
        extra = ""
        if row.resumed_from:
            extra = f" (resumed at {human(row.resumed_from)})"
        if row.verified is False:
            extra += " [CHECKSUM MISMATCH]"
        print(f"{mark} {naming.sanitize(row.file.name)} {human(row.bytes)}{extra}")
        if row.error:
            print(f"     {row.error}")

    manifest.write(out_dir / "manifest.json", manifest.build(record, results, token))
    print(f"\n{len(results) - len(failures)}/{len(results)} file(s) in {out_dir}")
    if args.manifest_out:
        manifest.write(Path(args.manifest_out), manifest.build(record, results, token))

    if failures and len(failures) == len(results):
        return EXIT_FAIL
    return EXIT_PARTIAL if failures else EXIT_OK


def cmd_verify(args: argparse.Namespace) -> int:
    manifest_path = Path(args.manifest)
    # Names in a manifest are relative to the directory it was written into.
    # Defaulting to the cwd made the README's own example fail.
    root = Path(args.root) if args.root else manifest_path.parent
    rows = manifest.verify_manifest(manifest_path, root)
    bad = sum(1 for row in rows if row["status"] != "ok")
    for row in rows:
        print(f"{row['status']:>9} {row['name']}")
    print(f"\n{len(rows) - bad}/{len(rows)} ok")
    if not rows:
        print("the manifest lists no files", file=sys.stderr)
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
    )
    parser.add_argument("--version", action="version", version=f"acadfetch {consts.__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def network_flags(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--rate",
            type=float,
            default=consts.DEFAULT_RATE,
            help="requests per second, global (default: %(default)s)",
        )
        p.add_argument(
            "--retries",
            type=int,
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
    fetch.add_argument("target", help="DOI, zenodo:<id>, figshare:<id>, arxiv:<id>, or URL")
    fetch.add_argument("-o", "--out", help="output directory")
    fetch.add_argument("-j", "--concurrency", type=int, default=consts.DEFAULT_CONCURRENCY)
    fetch.add_argument("--max-size", type=parse_size, help="abort above this total, e.g. 500MB")
    fetch.add_argument("--only", nargs="+", metavar="NAME", help="fetch only these file names")
    fetch.add_argument("--manifest-out", help="also write manifest.json here")
    fetch.add_argument("-n", "--dry-run", action="store_true")
    fetch.add_argument("-q", "--quiet", action="store_true")
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
    try:
        return args.func(args)
    except TargetError as exc:
        print(f"acadfetch: {exc}", file=sys.stderr)
        return EXIT_FAIL
    except AcFError as exc:
        print(f"acadfetch: {exc}", file=sys.stderr)
        return EXIT_FAIL
    except KeyboardInterrupt:
        print("\ninterrupted - partial files kept, rerun to resume", file=sys.stderr)
        return EXIT_INTERRUPT


if __name__ == "__main__":
    sys.exit(main())

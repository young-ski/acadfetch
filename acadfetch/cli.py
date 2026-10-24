"""Command line interface.

Exit codes are part of the contract: 0 all good, 1 everything failed or the
target was unusable, 2 partial success, 130 interrupted (partials are kept).
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

from . import consts, manifest, naming
from .client import Client
from .downloader import Options, fetch_record, human
from .errors import AcFError, PartialError, TargetError
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


class Printer:
    def __init__(self, quiet: bool = False) -> None:
        self.quiet = quiet
        self._last: dict[str, float] = {}

    def __call__(self, line: str) -> None:
        if self.quiet:
            return
        key = line.split(":")[0]
        now = time.monotonic()
        if now - self._last.get(key, 0.0) < 0.4:
            return
        self._last[key] = now
        print(line, flush=True)

    def info(self, line: str = "") -> None:
        if not self.quiet:
            print(line, flush=True)


def cmd_sources(_: argparse.Namespace) -> int:
    for name in registry.names():
        print(name)
    return EXIT_OK


def _client(args: argparse.Namespace) -> Client:
    return Client(rate=args.rate, retries=args.retries, headers=_headers(args.header))


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


def cmd_manifest(args: argparse.Namespace) -> int:
    client = _client(args)
    record = _resolve(args, client)
    data = manifest.build(record, [])
    out = Path(args.out or "manifest.json")
    manifest.write(out, data)
    print(f"{len(record.files)} file(s) -> {out}")
    _print_header(record)
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

    out_dir = Path(args.out or f"acadfetch-out/{record.source}-{_slug(record)}")
    opts = Options(
        out=out_dir,
        concurrency=args.concurrency,
        dry_run=args.dry_run,
        max_bytes=args.max_size,
        on_progress=Printer(args.quiet),
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

    manifest.write(out_dir / "manifest.json", manifest.build(record, results))
    print(f"\n{len(results) - len(failures)}/{len(results)} file(s) in {out_dir}")
    if args.manifest_out:
        manifest.write(Path(args.manifest_out), manifest.build(record, results))

    if failures and len(failures) == len(results):
        return EXIT_FAIL
    return EXIT_PARTIAL if failures else EXIT_OK


def cmd_verify(args: argparse.Namespace) -> int:
    root = Path(args.root or ".")
    rows = manifest.verify_manifest(Path(args.manifest), root)
    bad = 0
    for row in rows:
        if row["status"] != "ok":
            bad += 1
        print(f"{row['status']:>9} {row['name']}")
    print(f"\n{len(rows) - bad}/{len(rows)} ok")
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
            "--header",
            action="append",
            metavar="NAME:VALUE",
            help="extra request header, repeatable (e.g. 'Authorization: Bearer <token>')",
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
    ver.add_argument("--root", help="directory the manifest's names are relative to")
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

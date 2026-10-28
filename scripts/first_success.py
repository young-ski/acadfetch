"""The E1/E2/E3 instrument for the first-success bench.

One job: run the README's first command N times on this machine, in a fresh
directory each time, and append comparable rows to a CSV. It measures exactly
what a new user would get: default output directory, no token, no flags.

    python scripts/first_success.py --env-id E1-evening --csv .tmp/first-success.csv

A row counts as a pass only if the command exits 0 *and* `verify` then exits 0.
Automatic retries inside the tool are allowed; the attempt number records when a
pass finally happened. Nothing here writes to the repository.

E2 wraps the child in scripts/netem_proxy.py:

    python scripts/netem_proxy.py --port 8899 &
    python scripts/first_success.py --env-id E2-stall --proxy http://127.0.0.1:8899 ...

Exit: 0 when at least --min-pass attempts passed, 1 otherwise.
"""

from __future__ import annotations

import argparse
import csv
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

TARGET = "10.5281/zenodo.23612969"  # the README's first command
COLUMNS = [
    "env",
    "date",
    "os",
    "python",
    "tool_ref",
    "record",
    "attempt",
    "exit",
    "elapsed_s",
    "bytes",
    "part_kept",
    "verify",
    "http_status",
    "error",
]
HTTP = re.compile(r"HTTP (\d{3})")
args_target = TARGET
FAITHFUL = Path(__file__).resolve().parent.parent


def _ref() -> str:
    try:
        return (
            subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=FAITHFUL,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
            ).stdout.strip()
            or "unknown"
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _child_env(proxy: str | None, allow_token: bool, installed: bool) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if allow_token or not k.endswith("_TOKEN")}
    if not installed:
        env["PYTHONPATH"] = str(FAITHFUL)  # measure the checkout, not the install
    env["PYTHONIOENCODING"] = "utf-8"
    if proxy:
        for name in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
            env[name] = proxy
    return env


def _tally(directory: Path) -> tuple[int, bool]:
    kept = 0
    parts = False
    for path in directory.rglob("*"):
        if not path.is_file() or path.name == "manifest.json":
            continue
        kept += path.stat().st_size
        parts = parts or path.name.endswith((".part", ".part.meta.json"))
    return kept, parts


def _find_manifest(directory: Path) -> Path | None:
    """The default output is a subdirectory, so look one level down too."""
    direct = directory / "manifest.json"
    if direct.is_file():
        return direct
    return next(iter(sorted(directory.rglob("manifest.json"))), None)


def _cli(installed: bool, python: str) -> list[str]:
    """The command prefix: an installed binary, or this checkout via -m."""
    return ["acadfetch"] if installed else [python, "-m", "acadfetch"]


def _verify(directory: Path, python: str, env: dict[str, str], installed: bool) -> str:
    manifest = _find_manifest(directory)
    if manifest is None:
        return "no-manifest"
    try:
        done = subprocess.run(
            [*_cli(installed, python), "verify", str(manifest)],
            cwd=directory,
            env=env,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
        )
    except subprocess.TimeoutExpired:
        return "verify-timeout"
    return f"{done.returncode}"


def _row(
    env_id: str,
    attempt: int,
    code: str,
    elapsed: float,
    directory: Path,
    python: str,
    child_env: dict[str, str],
    output: str,
    ref: str,
    installed: bool,
) -> dict[str, str]:
    kept, parts = _tally(directory)
    statuses = sorted({m for m in HTTP.findall(output)})
    # The reason, not the verdict: an `acadfetch:` line first, else the line the
    # FAIL row is followed by.
    lines = [ln.strip() for ln in output.splitlines()]
    first = next((ln for ln in lines if "acadfetch:" in ln), "")
    if not first:
        for i, ln in enumerate(lines):
            if ln.startswith("FAIL") and i + 1 < len(lines):
                first = lines[i + 1]
                break
    return {
        "env": env_id,
        "date": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "os": f"{platform.system()} {platform.release()}",
        "python": platform.python_version(),
        "tool_ref": ref,
        "record": args_target,
        "attempt": str(attempt),
        "exit": code,
        "elapsed_s": f"{elapsed:.1f}",
        "bytes": str(kept),
        "part_kept": "1" if parts else "0",
        "verify": _verify(directory, python, child_env, installed),
        "http_status": "|".join(statuses),
        "error": first[:160].replace("\n", " "),
    }


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--target", default=TARGET, help="override the pinned record")
    ap.add_argument("--env-id", required=True, help="E1-evening, E2-stall, E3-actions-ubuntu, ...")
    ap.add_argument("--attempts", type=int, default=3)
    ap.add_argument("--timeout", type=float, default=600.0, help="per-attempt wall clock")
    ap.add_argument("--gap", type=float, default=5.0, help="seconds between attempts")
    ap.add_argument("--csv", default=".tmp/first-success.csv")
    ap.add_argument("--proxy", help="http proxy for the child (E2)")
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--allow-token", action="store_true")
    ap.add_argument(
        "--installed",
        action="store_true",
        help="measure the acadfetch on PATH (pipx/pip install), not this checkout",
    )
    ap.add_argument("--min-pass", type=int, default=2)
    ap.add_argument("--keep", action="store_true", help="do not delete attempt directories")
    args = ap.parse_args(argv)

    child_env = _child_env(args.proxy, args.allow_token, args.installed)
    if args.installed:
        try:
            shown = subprocess.run(
                ["acadfetch", "--version"],
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            print("acadfetch is not on PATH; drop --installed or install it first")
            return 2
        ref = f"installed:{shown}"
        print(f"measuring {shown} from PATH")
    else:
        ref = _ref()
    csv_path = Path(args.csv)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    fresh = not csv_path.exists() or csv_path.stat().st_size == 0
    global args_target
    args_target = args.target
    if args.target != TARGET:
        print(f"note: measuring {args.target}, not the pinned {TARGET}")
    passed = 0
    if args.keep:  # keep the evidence somewhere findable, not in %TEMP%
        Path(".tmp").mkdir(exist_ok=True)
    root = Path(
        tempfile.mkdtemp(prefix=f"afbench-{args.env_id}-", dir=".tmp" if args.keep else None)
    )

    try:
        with csv_path.open("a", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=COLUMNS)
            if fresh:
                writer.writeheader()
            for attempt in range(1, args.attempts + 1):
                work = Path(tempfile.mkdtemp(prefix=f"attempt{attempt}-", dir=root))
                started = time.monotonic()
                try:
                    done = subprocess.run(
                        [*_cli(args.installed, args.python), "fetch", args.target],
                        cwd=work,
                        env=child_env,
                        capture_output=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=args.timeout,
                    )
                    code, output = str(done.returncode), done.stdout + done.stderr
                except subprocess.TimeoutExpired as exc:
                    code = f"timeout>{args.timeout:.0f}s"
                    captured = exc.stdout or b""
                    output = (
                        captured.decode("utf-8", "replace")
                        if isinstance(captured, bytes)
                        else (captured or "")
                    )
                row = _row(
                    args.env_id,
                    attempt,
                    code,
                    time.monotonic() - started,
                    work,
                    args.python,
                    child_env,
                    output,
                    ref,
                    args.installed,
                )
                writer.writerow(row)
                fh.flush()
                ok = row["exit"] == "0" and row["verify"] == "0"
                passed += ok
                print(
                    f"attempt {attempt}: {'PASS' if ok else 'FAIL'} "
                    f"exit={row['exit']} verify={row['verify']} "
                    f"bytes={row['bytes']} part_kept={row['part_kept']} "
                    f"elapsed={row['elapsed_s']}s"
                )
                if not ok and row["error"]:
                    print(f"  {row['error']}")
                if not args.keep:
                    shutil.rmtree(work, ignore_errors=True)
                if attempt < args.attempts:
                    time.sleep(args.gap)
    finally:
        if not args.keep:
            shutil.rmtree(root, ignore_errors=True)

    print(f"\n{args.env_id}: {passed}/{args.attempts} passed (needs {args.min_pass}) -> {csv_path}")
    return 0 if passed >= args.min_pass else 1


if __name__ == "__main__":
    sys.exit(main())

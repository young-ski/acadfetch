"""Manual probe: interrupt a real download, then resume it.

Usage: python probe_resume.py [record-id] [interrupt-bytes]
"""

import json
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

RECORD = sys.argv[1] if len(sys.argv) > 1 else "23612969"
AT = int(sys.argv[2]) if len(sys.argv) > 2 else 400_000

meta = json.load(
    urllib.request.urlopen(
        urllib.request.Request(
            f"https://zenodo.org/api/records/{RECORD}", headers={"User-Agent": "acadfetch/0.1"}
        ),
        timeout=30,
    )
)
name = meta["files"][0]["key"]
expected = meta["files"][0]["size"]
print(f"record {RECORD}: {name!r} {expected} bytes")

OUT = Path("/tmp/probe")
shutil.rmtree(OUT, ignore_errors=True)
OUT.mkdir(parents=True)
part = OUT / (name + ".part")

CMD = [sys.executable, "-m", "acadfetch", "fetch", f"zenodo:{RECORD}", "-o", str(OUT)]
proc = subprocess.Popen(CMD, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
deadline = time.time() + 120
while time.time() < deadline:
    if part.exists() and part.stat().st_size >= AT:
        break
    time.sleep(0.05)
print("interrupting at", part.stat().st_size)
proc.terminate()
proc.wait(timeout=30)
time.sleep(0.4)
before = part.stat().st_size
print("size after kill:", before)

from acadfetch import cli  # noqa: E402

code = cli.main(["fetch", f"zenodo:{RECORD}", "-o", str(OUT)])
final = OUT / name
print("exit:", code)
print("final:", final.stat().st_size if final.exists() else "MISSING", "expected:", expected)
print("leftovers:", [p.name for p in OUT.iterdir()])

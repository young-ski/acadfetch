# Contributing

## Setup

```bash
python -m venv .venv && . .venv/bin/activate   # Windows: .venv\Scripts\activate
python -m pip install -e . pytest ruff
ruff check . && pytest -q
```

## Ground rules

- **No runtime dependencies.** urllib is enough; a dependency needs an argument.
- Every outbound request goes through `acadfetch.client.Client`. That is where
  rate limiting, retries and `Retry-After` live; bypassing it is a bug.
- **Repository metadata is hostile input.** Any field that becomes a filename
  goes through `naming.sanitize`; anything that becomes a path goes through
  `naming.unique`.
- Never trust `Content-Length` alone, and never rename a `.part` file before the
  checksum passes.
- A source adapter that cannot verify anything must still report `verified=null`.

## Tests

`pytest -q` must pass with no network. `tests/test_downloader.py` runs a local
HTTP server that can be told to ignore `Range`, truncate responses, or serve
bytes that fail the checksum — cover a resume path there before touching the
transfer loop.

`scripts/probe_resume.py <record-id>` interrupts a real download and resumes it.
It needs network; run it when you change `downloader.py`.

`scripts/slow_server.py` serves 16 MiB at 1 MiB/s with `Range` support, so speed,
ETA and resume rendering can be watched without depending on the network.

Progress must never be able to fail a download: it catches stream errors on its
own and every hook is a no-op when disabled. Keep it that way.

## Adding an adapter

See the issue template for the questions a PR must answer. Keep `match()` narrow:
`direct` matches every URL, so a broad pattern there shadows real adapters.

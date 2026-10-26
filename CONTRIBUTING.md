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

## Your first PR

1. Open an issue first if the change touches `downloader.py`, `client.py` or the
   manifest format — those carry the invariants and a wrong direction costs a
   weekend.
2. Adapters (`acadfetch/sources/`), tests and `docs/` need no issue: send them.
3. Run one test, then the suite:

   ```bash
   python -m pytest -q tests/test_downloader.py::test_resume_from_part
   python -m pytest -q            # offline, must be green
   ruff check . && ruff format --check .
   ```

4. Fill the PR template's invariant checklist honestly. "Not touched" is a fine
   answer; an unticked box with a reason is better than a false tick.
5. One concern per PR. An unrelated typo fix can ride along; an unrelated
   refactor cannot.

## Dependencies

The core is standard-library only, and a PR that adds a runtime dependency needs
to say why in the description. If the feature is optional, prefer an extra
(`[project.optional-dependencies]`) over a hard dependency — the default install
stays empty. `tests/test_packaging.py` fails the build either way, so you will
find out immediately.

## Review

Releases happen on demand, so review is not on a clock; expect a first reply
within about a week, sooner for anything touching bytes or tokens. Security
reports go through [`SECURITY.md`](SECURITY.md), not the issue tracker.

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

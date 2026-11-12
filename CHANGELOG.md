# Changelog

Notable changes, in [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
form. This project follows [Semantic Versioning](https://semver.org/) and is
pre-1.0: a minor bump may break a flag.


## [Unreleased]

## [0.7.0] - 2026-11-12

### Added

- `acadfetch.pooch`: a `pooch` downloader that supplies acadfetch's transport, so
  a `pooch.retrieve` in an existing workflow gets `Retry-After`, retried 5xx and
  WAF block pages, a resumed `.part` inside the attempt, and a credential header
  that is dropped when a redirect leaves the origin. `pooch` keeps the hashing and
  the move, so the integration cannot weaken verification.

  ```python
  import pooch
  from acadfetch.pooch import downloader

  path = pooch.retrieve(url, known_hash="md5:…", downloader=downloader)
  ```

  It is a plain callable with `Downloader(rate=…, retries=…, headers=…)`, because
  a callable is the whole contract `pooch` asks for. Two limits are documented
  rather than hidden: `pooch` hands each attempt a fresh temporary file, so a
  partial resumes within one attempt and not across `pooch`'s own retries; and
  `progressbar=True` lives inside `pooch`'s downloader, so a transfer through this
  one is silent. `pooch` is a test-only dependency; the package still imports
  nothing outside the standard library.

## [0.6.4] - 2026-11-12

A batch of correctness and honesty fixes. Two of them cost a user real bytes or
real trust; the rest are the drift that accumulates when a document and a parser
are edited on different days.

### Added

- `tests/test_claims.py`: the user-facing documentation is now a test input, so a
  doc that drifts from the code is a red build rather than a reader's bad
  afternoon. It asserts that every `--flag` a documented command uses exists, that
  every flag the parser accepts appears in the docs, that every `verified_reason`
  and `state` the code can emit is in the reference tables, that the README's exit
  codes equal the constants, that `SECURITY.md` names the current supported line,
  and that the coverage floor is the same number in `gates.yml`,
  `CONTRIBUTING.md` and `docs/releasing.md`.

  Written first as a detector and run against the previous code, it found six
  flags that existed only in `--help`: `--concurrency`, `--out` and `--quiet` were
  documented in short form only, and `--manifest-out`, `--retries` and `--version`
  were not documented at all.
- `docs/README.md`: an index that separates what is written for a user from what is
  written for a maintainer, and that labels the four process documents as history
  rather than as current behaviour. A test fails when a document is added to
  `docs/` and not indexed, because an index that is a partial list reads as the
  whole set.

### Changed

- CI no longer skips markdown-only changes. The docs are a test input now, so
  skipping the run exactly on the changes that can break them was the one way the
  new guard would rot unnoticed.
- The coverage floor is 90, up from 85. The suite measures 93% on Windows and
  Linux; the three points of headroom cover the platform branches each one cannot
  see.
- The progress line is built for the terminal it is drawn on. It asks for the
  column count instead of always assuming 120, and it counts CJK characters as two
  columns. A line that wrapped made the carriage-return redraw clear only the last
  visual row, so the line smeared across two rows on a narrow terminal.
- The speed and ETA estimate is an exponential average with a five-second time
  constant, instead of the difference between the first and last sample of a
  four-second window. One bursty interval no longer re-anchors the whole estimate.
- A dry run prints `size not published` where it used to print `(?)`. arXiv really
  does publish no size, and a question mark in a size column reads as a defect
  rather than as an absence.
- `acadfetch sources`, the no-target hint, the README and the nightly smoke no
  longer present a direct URL as unconditionally fetchable: figshare's CDN answers
  403 with a WAF block page from some networks. The target is kept (it is a real
  file, and `--checksum` verifies it), the claim is honest about the WAF, and the
  nightly run distinguishes "a canary target is unreachable" from "our code
  broke" instead of raising the same alarm for both.
- `CONTRIBUTING.md` installs `mypy` alongside the other test tools, so the gate it
  tells you to run can actually run.
- `SECURITY.md` lists `0.6.x`, not `0.1.x`, as the supported line, and no longer
  describes `verify --against-remote` as a roadmap item; it shipped in 0.3.0.
- `docs/troubleshooting.md` no longer mentions `FIGSHARE_TOKEN`, removed in 0.2.0.
- The 2026-11 planning document moved from the repository root to `docs/`.

### Fixed

- A server that answered a `Range` request with the whole object (200) used to be
  handled by truncating the `.part` before the new body arrived, so a restart that
  died early destroyed bytes that had already been paid for — 6 MiB kept became
  1 MiB kept. The restart is now written beside the part and swapped in only once
  a whole body has arrived and its length agrees; on any failure the old part and
  its sidecar are left exactly as they were.
- Two deletions whose absence changes behaviour were silently best-effort.
  `atomic.unlink` swallows a sharing violation on purpose — losing a race with a
  reader is normally harmless — but on a Windows box where the `.part` is held
  open, a checksum mismatch went on to re-download *onto the bytes just proven
  wrong*, and an over-limit part stayed behind for the next run to resume straight
  back into the same wall. Both now report what happened and stop, and the mismatch
  keeps `verified: false` so it stays legible downstream.
- `verify --against-remote` reported `remote_mismatch` — a hard failure, even when
  the local hashes were fine — for a row the source now publishes under a different
  algorithm (`sha256` where the manifest recorded `md5`). Comparing two different
  algorithms proves nothing, so it is reported `remote_no_checksum` with both
  algorithm names in the detail. Calling a legitimate change tampering was the one
  way this check could destroy its own credibility.
- `resumed_from` (in `--json` and in the manifest) was overwritten on every retry
  with that retry's offset, so a download that started from nothing could report
  `(resumed at 1.6 MiB)`. It now means what `docs/json-report.md` always said:
  bytes on disk when the run started.
- A 403 that ended the retry loop terminally returned the bare HTTP error, which
  skipped the "what to try next" hint the other failures carry — the one path a
  user is most likely to hit on a blocked network. The hint is now attached.
- `verified_reason` for a failed row said `transfer-failed` in `manifest.json` but
  `no-checksum-published` in the `--json` report. Both now go through one
  function, so the two documents describe the same run the same way.
- A manifest written without `--record-full-url` has its credential query
  parameters stripped, so such a URL cannot be resolved again. `--against-remote`
  now says that on stderr instead of leaving a page of `remote_unavailable` to be
  interpreted.

## [0.6.3] - 2026-11-12

### Fixed

- Two runs into one output directory wrote the same `.part` and the survivor was
  a file neither had finished. The directory is locked with `.acadfetch.lock`
  (owner pid, checked non-intrusively); a live owner refuses the second run with
  the pid and the file to delete, a dead or unreadable owner is taken over, and
  the lock is released on every exit path.

## [0.6.2] - 2026-11-12

### Fixed

- A `416` on a kept `.part` was always read as ``already complete``, so a part
  left by an older object was renamed into place with nothing checked when the
  file had no size or checksum. The answer now needs evidence: a matching
  checksum, or a `Content-Range: bytes */N` equal to the part. Otherwise the
  part is dropped and the file fetched again.
- The Zenodo `is_latest` follow-the-latest branch is dormant (the API stopped
  sending the field); a test pins both the follow and the kept target instead of
  leaving it unexercised.

## [0.6.1] - 2026-11-11

### Changed

- The digest is computed while the bytes go by: SHA-256 and whatever the source
  published are updated in the transfer loop, a resumed part is seeded once, and
  the verification and the manifest reuse the result. A finished file is no
  longer read for `verify` and again for `manifest.json`; a reused file is read
  once for both its verdict and its hash. At 64 MiB that is two full passes
  (128 MiB) gone per file.
- In-flight requests are capped at `concurrency * 2` and refilled as they
  complete, instead of submitting one future per file up front.

## [0.6.0] - 2026-11-11

### Added

- `--cite` and `CITATION.cff`: how to cite the tool, separate from how to cite
  the data it fetched.
- `docs/demo.md` (a real 30-second transcript) and `docs/integrations.md`
  (aria2, Make, CI, Snakemake).
- `--glob PATTERN…` selects files by shell pattern and combines with `--only`.
- `-w/--urls PATH` writes an aria2-compatible list (URL, `out=`, `dir=`) instead
  of downloading; `-` is stdout and the header moves to stderr, so it can be
  piped. Nothing is fetched and no manifest is written.

### CI

- mypy runs on one cell; seven errors it found were fixed and `direct.py`
  reached 100% coverage. `Typing :: Typed` now costs something to keep.

## [0.5.0] - 2026-11-11

### Added

- `--verbose` / `ACADFETCH_DEBUG=1`: request, retry and traceback tracing on
  stderr, with credential headers and presigned query parameters masked. stdout,
  `--json` and the exit code are untouched; a log line can never fail a run.

## [0.4.0] - 2026-11-11

### Added

- `--timeout SECONDS`, `--proxy URL`, `--no-proxy` and `--ca-bundle PATH` on every
  network command. A campus proxy or a TLS-inspecting middlebox used to need
  environment variables or hope; `--proxy` overrides the environment, `--no-proxy`
  ignores it, and `--ca-bundle` trusts a PEM instead of the system store.

## [0.3.1] - 2026-11-11

### CI

- A `workflow_dispatch` run at a branch used to publish: the guard only runs on
  tags and `publish` accepted a skipped guard, so an untagged version could
  reach PyPI with no post-publish smoke. `publish` now requires a tag ref; a
  branch dispatch runs the gates and stops.
- The publish and attestation actions are pinned to commit SHAs (all six,
  `# vX.Y.Z` kept for legibility), so a moved tag cannot change what runs.

## [0.3.0] - 2026-11-11

### Added

- `verify --against-remote`: resolve `target` again and compare each recorded
  checksum with what the source publishes now, so a manifest that was rewritten
  is caught even when the local hashes still agree with it. Off by default;
  `remote_mismatch` fails, `--require-remote` also fails on an unreachable
  source, and a successful check records `remote_checked_at`. It was checked
  against a real Zenodo record: an untouched manifest matches, a rewritten row
  fails.

## [0.2.1] - 2026-11-11

### Fixed

- `naming.unique` was a check-then-add on a set shared by every worker, so two
  transfers could receive the same name, one renamed over the other, and the
  manifest kept two rows with one name and a lost file. On the free-threaded
  3.15.1 build that happened in 36 of 500 stress rounds; with the allocation
  locked, 0 of 500. Free-threaded 3.15.1 now runs the suite, `-W error` and a
  `-j 8` end-to-end stress green; CI still does not cover that build.

## [0.2.0] - 2026-11-10

### Changed

- A usage error (a bad flag value, a missing target) exits `64` instead of `2`.
  `2` is "partial success", so a wrapper that retried on `2` used to retry a typo.
- `--only` that matches nothing lists the file names that do exist, in the human
  error and in the `--json` document, instead of only saying it did not match.

- `manifest.json` is schema 3. Every row carries `selected`; a `--only` run
  writes `state: "not-selected"` for the files it did not ask for instead of
  `missing`, and `verify` reports those as `skipped`, so a successful partial
  fetch no longer fails verification. `acadfetch manifest` writes `planned` for
  the same reason. `--require-all` counts skipped rows as failures again.
  Schema 1 and 2 still read; the field and state reference is
  [docs/manifest.md](docs/manifest.md).
- `verify --json` counts `skipped` in `summary`, keeps `sha256` in its rows, and
  reports `ok_run: false` for an empty manifest.

- Schema 2 stays the readable shape of the 0.1.1 era (name on disk,
  `verified_reason`), and `verify` still warns about schema 1. Nothing written
  before 0.2.0 has to be re-fetched to be verified.

### Removed

- figshare support. Its API's WAF block page was the most fragile corner of
  `client.py` and bought only a third source. `figshare:N`, `10.6084/…` and
  article URLs are still recognised and answer with the conversion instead of a
  generic unknown target; `sources` ends with one line pointing at it. Existing
  figshare manifests still verify, and `--checksum` restores the check the API
  used to give ([docs/migration-0.2.md](docs/migration-0.2.md)).
- `File.fallbacks` (figshare was its only producer) and `FIGSHARE_TOKEN`.

### Added

- `--checksum md5:<hex>|sha256:<hex>`: verify against a checksum you supply,
  refused before any request when malformed, recorded in the manifest with
  `checksum_source: "cli"`. This is the migration path for a removed source and
  a way to get strong verification out of a direct URL.

- A run that cannot fit is refused before the first byte is transferred,
  naming the shortfall and keeping 64 MiB spare. What is already on disk is
  subtracted, so a resume is judged on its remainder instead of the whole record.
  A dry run reports free space and marks a shortfall instead of failing.
- Each finished file is flushed before the rename, so the rename cannot outrun
  the bytes. `--no-fsync` turns it off for speed.

- `fetch --json` and `verify --json` print one report document to stdout and
  send progress to stderr, so `jq` sees JSON and nothing else. Exit codes are
  unchanged, an early failure still yields a document with `error`, and a failed
  transfer names the `part` file it kept. `report` versions the shape; fields are
  append-only.
- A checksum mismatch sets `verified: false` instead of leaving it `null`, which
  is also what makes the human `[CHECKSUM MISMATCH]` marker appear.
- Manifest rows carry `final_url`, `etag`, `http_status` and `retrieved_at`, so a
  year later the record says where the bytes came from, what validator they
  carried and when they arrived, not only what was asked for.
- Credential query parameters (`X-Amz-Signature`, `X-Amz-Credential`, `token`, …)
  are stripped from the manifest, the JSON report, error lines and the default
  directory name; `url_redacted` records that it happened and
  `--record-full-url` keeps the original string.

### Fixed

- One unexpected worker exception (a directory sitting on a final name, a
  permission error on rename) ended the whole batch, printed a traceback and
  lost the manifest for the files that had finished. Each future is caught now
  and becomes one `failed` row; the rest of the batch and its manifest survive.
- A truncated metadata response escaped as a traceback too: `IncompleteRead` is
  an `HTTPException`, which the API path never caught. The body read is inside
  the retry loop now, so a cut body is retried, and a permanent one is one line.
- `-o` pointing at an existing file failed as a raw `FileExistsError`. It says
  `-o must be a directory` before anything is fetched.
- A dry run with too little space failed although nothing was being written; it
  now reports the shortfall with `disk.ok: false` and exits 0.
- `--max-size` read as `HTTP 0 for …`; a local refusal says `refused locally`
  without inventing a status code.

- arXiv legacy ids were cut at the first `v`, so `solv-int/9701001v2` became
  `sol` — a DOI that does not exist (`10.48550/arXiv.sol`), a file called
  `sol.pdf`, a version of `-int/9701001v2`, and one directory shared by every
  `solv-int` paper, where the second was skipped as already present. Only the
  trailing `v<digits>` is stripped now, the archive prefix is kept in the file
  name and the DOI, and two legacy papers no longer collide.
- Every arXiv record claimed `arxiv-nonexclusive-distrib`. arXiv lets a submitter
  choose CC BY, CC BY-SA, CC0 and others, so it was wrong for many papers. The
  `arxiv:license` arXiv reports is used; when it reports none, the licence is
  `unknown` rather than a guess.

- A repository file called `manifest.json` was overwritten by our manifest, and
  one called `x.part` was swallowed by `x`'s sidecar (`x` ended up with mixed
  bytes and `x.part` disappeared). The names the tool itself writes are reserved
  before allocation, so the repository's file is kept as `manifest (2).json` /
  `x (2).part` instead.

- A DOI that resolved to a web page (Mendeley, Dryad, an institutional
  repository's front door) was handed to the direct adapter, and the HTML was
  written out as the dataset — `state: "downloaded"`, a SHA-256 over the markup,
  and a green `verify`. A resolved URL that no adapter claims is now probed once,
  and an HTML answer is refused with the page named.
- The default output directory was the URL's last segment only, so
  `data.csv` from two hosts shared a directory and the second was skipped as
  already present. The name now carries the host and a short URL hash.
- "Already there" meant "same size" when no checksum was published, so a
  different file of the same length was silently kept. Without a checksum, the
  size now needs one more tie: an explicit `-o`, or a manifest in that directory
  that recorded the same URL. `--force` re-fetches regardless.

- Checksum headers on a direct URL were effectively dead code: the lookup was
  case-sensitive, and `Content-MD5` (base64 by standard) was fed to a hex
  parser, so a published header never became a verification. Lookups are
  case-insensitive now, `Content-MD5` and `x-amz-checksum-sha256` are
  base64-decoded, a bare hex `X-Checksum-Sha256` is still accepted, SHA-256
  beats MD5, and `Content-Length` is read the same way. A header-supplied
  checksum is recorded as `checksum_source: "header"`.

- A body served as `text/html` for a `.pdf`/`.zip`/`.csv`/… was written out and
  reported `downloaded`, so a WAF block page or a login redirect became a file
  that the *next* run skipped as already present. Those bodies are refused now,
  with the reason named, and the markup is not kept for resume.
- A failed entry in a schema 2 manifest said `no-checksum-published`, which read
  as if the bytes were checked; it says `transfer-failed`.

- `--rate 0` printed a traceback from deep inside the limiter, `-j 0` and
  `--retries -1` were silently clamped, and `-n` ignored `--only` entirely.
  Each flag now validates itself and says what it wants.
- A failure says the next command (`rerun to resume`, `--rate 1 -j 1`,
  `--retry-403 2`, or a DNS/VPN pointer) instead of only what went wrong, and
  `--max-size` reads as a local refusal rather than `HTTP 0`.
- `--only a.pdf <target>` used to make argparse eat the target and then blame
  the user for not supplying one; the target is recovered.
- `fetch` with no arguments offered only an argparse usage line: it now says
  what to type, `--help` lists the target forms and the exit codes, and
  `sources` prints one usable example per source.
- `-n` prints the directory it would write to, which is how the default
  `acadfetch-out/` stops being a surprise.

- A single 1 s gap in a response body ended the transfer: `socket.SocketIO`
  latches its first timeout and then refuses every later read, so a stall on a
  slow or mobile link cost the whole file. The latch is cleared on each poll
  timeout. Measured on a 1 MiB/s link with one 2 s stall: 0/3 before, 3/3 after,
  no retries needed.
- `IncompleteRead` from a body cut short is an `HTTPException`, not an
  `OSError`, so it escaped the retry loop and the CLI as a traceback and took
  the rest of the batch with it.
- A spent retry budget deleted the `.part`, the opposite of what this README
  and the "flaky VPN at 80%" pitch promise. The partial is kept now, its size is
  reported instead of `0 B`, and the failure names the file so a rerun resumes.
  Bytes that cannot be trusted -- a checksum mismatch -- are still dropped.

### Security

- A redirect that stayed on the same host but dropped from `https` to `http`
  kept the `Authorization` header, so the token went out in clear text. It is
  removed now, with a warning naming the host; `--allow-http` keeps it for a
  mirror that needs it. A cross-origin hop also drops `Cookie` and
  `Proxy-Authorization`, which used to travel to every redirect target.

- `urllib` copies every header onto a redirect, so `ZENODO_TOKEN` followed a
  download to whatever host the API pointed at, contradicting the README. A
  redirect that leaves the origin now drops `Authorization`, and a presigned URL
  (`X-Amz-Signature`) gets none to begin with, since S3 rejects a request that
  carries both. An `https`→`http` hop is recorded on the client instead of
  passing unnoticed.

### Tests

- The fault-injecting HTTP server moved to `tests/conftest.py` and gained
  `answers_416` and a recorded `Range` log, so a new adapter's tests are a
  fixture away instead of a hundred lines of private class.
- Four paths that only a human could check before are now deterministic: a real
  delivered `SIGINT` mid-read, `416` on a complete part, a stale sidecar URL, and
  a `.part` larger than the file.
- An interrupt now reports the bytes it kept and the file they are in, and stops
  the run instead of moving to the next file.

### CI

- A nightly job downloads and verifies three fixed public records and opens (or
  appends to) a `live-smoke` issue on failure, closing it on recovery. It is not
  on push/PR, so a slow repository never blocks a merge.
- E2 in the first-success bench no longer has `continue-on-error`: the stall
  regression that it was written to catch can be red again.

- CI and the release called two hand-copied gate lists, and they had already
  drifted: the release ran bare `pytest` with no coverage floor, no `-W error`, no
  `twine check` and no wheel-content assertions, under a comment claiming they
  matched. Both now call one `gates.yml`.
- A release is guarded: the tag must be an ancestor of `main`, it must equal the
  packaged version, and releases serialise instead of racing the same filename.
- `pypa/gh-action-pypi-publish` is pinned to a tag instead of the `release/v1`
  branch, artifacts get a build-provenance attestation, and a post-publish job
  installs the version from PyPI and resolves a real record -- so a broken wheel
  is a red run, not a bug report.
- The suite runs 3.10-3.15, but lint runs on one cell per OS instead of fifteen,
  and `-W error` on one cell: the same coverage for a fraction of the minutes.
- `docs/releasing.md` is the checklist, linked from CONTRIBUTING.
- Dependabot watches pip and the actions weekly.

### Documentation

- `docs/json-report.md`: the whole `--json` contract, field by field, with the
  stability rule (`report` versions it, fields are appended), redaction, exit
  codes and `jq` recipes. A test keeps the field list and the document in step.

- The fourth "works as typed" command was `https://example.org/dataset.tar.gz`,
  a reserved domain that answers 404; so was the direct example in `sources`.
  Both are now a real ndownloader URL, which doubles as the figshare recipe.
  `fetch` with no target prints the three commands instead of talking about
  `--only`, and `fetch --help` carries the target forms and the exit codes.
- The platform table claimed Windows was tested on every supported Python (it is
  tested on 3.10, 3.13, 3.15) and that macOS ran 3.12 and 3.14 (it is 3.12 and
  3.15). The development install dropped `pytest` from a command that then ran
  `pytest`.
- "Refused before the first request" meant before the first *byte*: the record
  is resolved first. The progress line's pipe behaviour is described with the
  extra frame it actually emits per file.

- The usage block was placeholder-deep: two of the first three ids did not
  resolve. It leads with three real targets plus a real direct URL, labels the
  remaining placeholders, and offers `uvx` so a first run needs no install.
- `docs/troubleshooting.md` indexes the failures people actually hit (stall, DNS
  blackhole, figshare 403, block page, `MISMATCH`, escaped names, long paths) and
  what to do about each.
- README no longer over-promises: the progress line's piped behaviour matches
  what it does, and the "resume keeps the partial" claim is true rather than
  aspirational.
- `acadfetch-out/` is in `.gitignore`; `-n` prints it.
- CONTRIBUTING lists the three gates CI actually runs (coverage floor and
  `-W error` were local-green-but-CI-red traps).

## [0.1.1] - 2026-10-26

### Fixed

- `os.replace` fails on NTFS while any other handle holds the target, and the
  manifest write happens after the download: a concurrent reader turned a
  successful transfer into a traceback with exit 1 and an orphan `.tmp`.
  Replacing and reading a file now retry a sharing violation briefly; a lock
  that outlasts them is one line and exit 2, with no leftovers.
- Ctrl-C could not stop a transfer. `KeyboardInterrupt` only reaches the main
  thread, so with `-j 3` the workers downloaded to the end and renamed the file,
  and a silent socket swallowed the interrupt for the full 60 s timeout. Reads
  poll, an interrupt closes the live responses, and the run now returns what
  finished, keeps the `.part`, writes the manifest and exits 130.
- arXiv requests go over https and close their response; the API's current
  `Connection: close` was hiding a missing `with`, and `resolve()` had no test.
- The unknown-target error is one line again.

### Changed

- `verify` reports a name that leaves the root as `escaped` and exits 1 instead
  of hashing whatever it points at. A manifest is a document you can be handed;
  `../sibling/x`, an absolute path or a symlink leading out used to be verified
  silently, which is how a green result stops meaning anything. `--allow-escape`
  restores the old behaviour, `--root` decides what counts as inside. Paths that
  use `..` but stay inside keep working, and the Windows-drive form no longer
  silently becomes a relative directory on POSIX.

- `verify` resolved names against the working directory instead of the
  manifest's own, so the command in this README failed on healthy data.
- `verify` dumped a traceback for a missing file, a directory, invalid JSON or a
  future schema; it now prints one line and exits 1, and reports an entry with
  no name as `malformed`.
- A malformed `Content-Length` (e.g. `12, 12`) escaped as a `ValueError`; a body
  shorter than the declared length was kept; a declared length disagreeing with
  the published size was ignored. All three are failures now.
- A `206` from an offset other than the one requested was appended to the
  partial file, splicing two offsets. `Content-Range` decides, and an ambiguous
  answer restarts instead of guessing.
- Retry budgets multiplied (client retries × transfer retries): a permanent 500
  cost 25 requests, and `--retries` never reached the transfer layer. One budget
  now owns it — 5 requests at defaults, 1 at `--retries 0` — and advisory `HEAD`
  calls stop retrying.
- `Retry-After: 0` meant no gap at all; there is a small jittered floor now.
- A `--max-size` trip left the over-limit `.part` behind for the next run.

### Added

- `tests/test_packaging.py`: non-stdlib import sweep, no declared dependencies,
  and `version` matching `consts.__version__`.
- CI: coverage floor, deprecation warnings as errors, job timeouts, `twine
  check`, wheel/sdist content assertions, and a `--no-deps` install.
- `SECURITY.md`, a bug template, a PR template with the invariants as a
  checklist, `CODEOWNERS`, a first-PR path in `CONTRIBUTING.md`.

## [0.1.0] - 2026-10-25

First release. Nothing here has a previous behaviour to be compatible with,
so it is all "Added" — the list doubles as an honest statement of scope.

### Added

- `fetch`: resumable downloads built on `.part` files, `Range` + `If-Range`,
  and a rename only after verification, so an interrupted run never leaves a
  file that looks complete.
- `verify`: re-hashes every file in a manifest and exits non-zero on a
  mismatch, a missing file, or an unknown schema.
- `manifest`: `manifest.json` with DOI, license, publisher, per-file SHA-256
  and the checksum the repository published, so a download can be cited and
  re-checked later.
- Adapters for Zenodo, figshare, arXiv, doi.org and plain URLs behind one
  registry; DOIs resolve through doi.org and land back in the right adapter.
- One global token bucket for every outbound request, `Retry-After` honoured
  up to 120 s, jittered exponential backoff on 408/425/429/5xx and network
  errors.
- Per-source credentials from `$ZENODO_TOKEN` / `$FIGSHARE_TOKEN`, sent only
  to their own repository; the manifest records the variable name and the
  last four characters, never the token.
- Deprecation-free packaging: PEP 639 `License-Expression`, `py.typed`, CI on
  Linux and Windows for CPython 3.10 through 3.14.
- Publishing over OIDC from the `release.yml` workflow — no PyPI token.

### Fixed

- A WAF block page (403 with an HTML or empty body, e.g. from
  `api.figshare.com`) was treated as a final permission error and made whole
  records unfetchable. It is now retried twice with the partial file kept,
  then the legacy per-file endpoint is tried; a 403 that really is a
  permission error still fails on the first answer. `--retry-403` controls
  the count.
- Re-running a completed `fetch` printed a `0 file(s) 0 B` progress frame
  directly above the `1/1 file(s)` tally.

### Known limits

- Windows ARM and the free-threaded build (3.15t) are untested.
- macOS is best effort: it runs in CI but does not block a release.
- Tokens come from the environment only: no interactive login, no refresh.
- No search or pagination — you bring the target.
- figshare was removed in 0.2.0; its API's WAF answered 403 outright from some
  networks (see `--retry-403`).

[Unreleased]: https://github.com/young-ski/acadfetch/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/young-ski/acadfetch/releases/tag/v0.1.1
[0.1.0]: https://github.com/young-ski/acadfetch/releases/tag/v0.1.0

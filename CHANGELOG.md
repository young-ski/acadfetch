# Changelog

Notable changes, in [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
form. This project follows [Semantic Versioning](https://semver.org/) and is
pre-1.0: a minor bump may break a flag.

## [Unreleased]

### Changed

- `verify` reports a name that leaves the root as `escaped` and exits 1 instead
  of hashing whatever it points at. A manifest is a document you can be handed;
  `../sibling/x`, an absolute path or a symlink leading out used to be verified
  silently, which is how a green result stops meaning anything. `--allow-escape`
  restores the old behaviour, `--root` decides what counts as inside. Paths that
  use `..` but stay inside keep working, and the Windows-drive form no longer
  silently becomes a relative directory on POSIX.

### Fixed

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

- Windows ARM and Python 3.15 are untested.
- macOS is best effort: it runs in CI but does not block a release.
- Tokens come from the environment only: no interactive login, no refresh.
- No search or pagination — you bring the target.
- figshare was verified end to end from a Japanese network only; the API's
  WAF answers 403 outright from some networks (see `--retry-403`).

[Unreleased]: https://github.com/young-ski/acadfetch/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/young-ski/acadfetch/releases/tag/v0.1.0

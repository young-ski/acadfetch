# Changelog

Notable changes, in [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
form. This project follows [Semantic Versioning](https://semver.org/) and is
pre-1.0: a minor bump may break a flag.

## [Unreleased]

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
- Tokens come from the environment only: no interactive login, no refresh.
- No search or pagination — you bring the target.
- figshare was verified end to end from a Japanese network only; the API's
  WAF answers 403 outright from some networks (see `--retry-403`).

[Unreleased]: https://github.com/young-ski/acadfetch/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/young-ski/acadfetch/releases/tag/v0.1.0

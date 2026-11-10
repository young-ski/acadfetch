# acadfetch

[![CI](https://github.com/young-ski/acadfetch/actions/workflows/ci.yml/badge.svg)](https://github.com/young-ski/acadfetch/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/acadfetch.svg)](https://pypi.org/project/acadfetch/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Batch-download files from academic data repositories — resumably, rate-limited, and
checked against the checksums the repository published.

```
$ acadfetch fetch 10.5281/zenodo.23612969 -o ./data
title   : Dinler Tarihi Alanında Güncel Konular ve Akademik Değerlendirmeler
doi     : 10.5281/zenodo.23612969
license : cc-by-4.0
files   : 1, 1.6 MiB
landing : https://zenodo.org/records/23612969
Dinler Tarihi ... .pdf  1.2 MiB/1.6 MiB  312.4 KiB/s  ETA 0:02
ok   Dinler Tarihi ... .pdf 1.6 MiB (resumed at 512.0 KiB)

1/1 file(s) in ./data
```

**What it is not:** a search engine, a scraper for paywalled publishers, or a
citation manager. It is the boring part in the middle — the part that gets killed
by a flaky VPN at 80%, and the part that nobody writes down.

## Why another downloader

Because `curl -O` in a loop loses three things that matter for research data:

| Problem | What happens with a plain loop | What acadfetch does |
| --- | --- | --- |
| Connection drops at 80% | restart from zero | resumes with `Range` + `If-Range`, keeps the partial |
| Silent corruption | nobody notices until analysis breaks | verifies the repository's `md5`/`sha256`, re-downloads on mismatch |
| "Which version did I actually use?" | untraceable | writes `manifest.json` with DOI, license, per-file SHA-256 |
| Getting rate-limited into a 403 | hammering, then blocked | one global token bucket, honours `Retry-After` |

The manifest is the point. A dataset you cannot re-verify is a dataset you cannot
cite.

## Install

Python 3.10–3.15. No runtime dependencies.

Try it without installing anything:

```bash
uvx acadfetch fetch 10.5281/zenodo.23612969   # uv
```

Or keep it on the PATH:

```bash
pipx install acadfetch
```

From a checkout, for development:

```bash
git clone https://github.com/young-ski/acadfetch && cd acadfetch
python -m pip install -e . && python -m pytest -q
```

## Supported platforms

| Platform | Status |
| --- | --- |
| Linux | tested — CI, every supported Python |
| Windows | tested — CI, every supported Python |
| macOS | best effort — CI runs it on 3.12 and 3.14, a failure is a bug to fix but does not hold a release |
| Windows ARM | untested |
| Python 3.15 | tested — CI, on Linux and Windows |

## Usage

These work as typed:

```bash
acadfetch fetch 10.5281/zenodo.23612969        # a DOI
acadfetch fetch arxiv:1706.03762               # an arXiv id
acadfetch fetch https://example.org/dataset.tar.gz   # any URL
```

figshare was removed in 0.2.0: pass the article's download URL instead, and
[`--checksum`](docs/migration-0.2.md) keeps the verification the API used to
give you.

The rest of the surface:

```bash
acadfetch fetch 10.5281/zenodo.23612969 -n     # dry run: which dir, which files
acadfetch fetch 10.5281/zenodo.23612969 --only raw.tar.gz
acadfetch fetch 10.5281/zenodo.23612969 --max-size 2GB   # refuse anything bigger
acadfetch fetch 10.5281/zenodo.23612969 -j 1 --rate 0.5  # be a good citizen
acadfetch manifest 10.5281/zenodo.23612969 -o m.json     # metadata only, no bytes
acadfetch fetch zenodo:123456 --header "Authorization: Bearer $TOKEN"   # restricted

acadfetch verify ./data/manifest.json          # re-hash what you downloaded
acadfetch sources                              # adapters, with an example each
```

`10.5281/zenodo.123456`, `zenodo:123456` and `arxiv:2401.12345` are placeholders;
use any real id.

### In a pipeline

`--json` puts one report document on stdout and moves progress to stderr, so the
two never interleave:

```bash
acadfetch fetch zenodo:123456 --json | jq -r '.files[] | select(.state=="failed") | .name'
```

Exit codes are unchanged, and a failure before any transfer still produces a
document with `error` set. `report` is the document's schema version (now `2`):
fields are added, never removed or given a new meaning, and `state` may gain
values — treat an unknown one as non-terminal. `verify --json` reports per-file
`MISMATCH` rows the same way, and its `summary` counts `skipped` rows
separately. At the end of a failed transfer, `part` names the `.part` file that
was kept, so a retry can resume instead of starting over. A 403 that carries a
block page is retried; see [docs/troubleshooting.md](docs/troubleshooting.md).

Progress is one aggregate line for the whole run
(`3 file(s)  12.4/44.1 MiB  1.8 MiB/s  ETA 0:17`), redrawn in place on a TTY.
When stdout is a pipe it is rewritten as a fresh whole line at most every 5 s, so
a CI log stays greppable without becoming a wall. `-q` turns it off.

Output defaults to `acadfetch-out/<source>-<id>` (a URL adds its host and a short
hash), and `-n` prints it before
you spend bandwidth. Files land in `<name>.part` and are renamed only after the
checksum matches, so an interrupted or failed run never leaves something that
*looks* finished -- and never throws away the bytes it already paid for: rerun and
it resumes. `acadfetch-out/` is worth adding to a project's `.gitignore`; this
repository already has it.

The default directory names the target, not only its filename — `data.csv` from
two hosts is two datasets, and they do not share a directory. A file already
there is reused when its checksum matches; with no checksum, the size plus one
more tie (an explicit `-o`, or a manifest in that directory that recorded the
same URL) is required, and `--force` skips the question entirely.

A DOI that resolves to a web page instead of a file is refused, naming the
page: fetching the landing page as if it were the dataset is how a repository's
front door ends up being cited as a data file.

A run that cannot fit is refused before the first request, naming the shortfall:
`needs 41.2 GiB but 12.0 GiB is free`. What is already on disk is subtracted, so
a resume is judged on the remainder. Each finished file is flushed before its
rename (`--no-fsync` skips that; it is faster and weaker against a power cut).

Exit codes: `0` success, `1` failure, `2` partial, `64` bad usage, `130` interrupted
(rerun to resume).

## Tokens

`ZENODO_TOKEN` is read from the environment and sent only to Zenodo, so a token
never travels to a host that has no business seeing it -- including across a
redirect: a hop to another host (a storage CDN, a download mirror) goes out
without it, a hop from https to http drops it with a warning (`--allow-http`
keeps it), and a presigned URL gets no `Authorization` at all, because S3
rejects a request that carries both. Cookies and proxy credentials are dropped
on any cross-origin hop as well. `--header` still overrides, `--no-token`
ignores the environment, and the manifest records the variable name plus the
last four characters only:

```bash
export ZENODO_TOKEN=...            # restricted records, higher rate limits
acadfetch fetch zenodo:123456      # prints: auth    : token from $ZENODO_TOKEN (…9f2c)
```

## Verification and the manifest

Every completed run writes `manifest.json`:

```json
{
  "schema": 3,
  "doi": "10.5281/zenodo.123456",
  "license": "cc-by-4.0",
  "files": [
    { "name": "raw.tar.gz", "size": 8123, "checksum": "md5:9f2c…",
      "sha256": "b41d…", "state": "downloaded", "selected": true,
      "verified": true, "verified_reason": "matched-md5" },
    { "name": "notes.txt", "size": 12, "state": "not-selected", "selected": false,
      "sha256": null, "verified": null, "verified_reason": "not-selected" }
  ]
}
```

The manifest describes **the record**, not the run: `--only` still writes a row
for every file, so `verify` can tell "not fetched" from "fetched and then lost",
and a successful `--only` run verifies green. Add `--require-all` when you want
the whole record accounted for. Each fetched row also records `final_url`,
`etag`, `http_status` and `retrieved_at`, and credential query parameters are
stripped from every URL (`--record-full-url` keeps them). Full field and state
reference: [docs/manifest.md](docs/manifest.md).

`name` is the file *on disk*, which can differ from the repository's name when two
files collide. `verified_reason` separates the two kinds of `verified: null`:
`no-checksum-published` (arXiv) from `unparseable-checksum` (a repository that
published something we cannot name an algorithm for). Schema 1 and 2 manifests
still read, and `verify` says so for schema 1, because their names were recorded
before duplicates were resolved.

`acadfetch verify` recomputes SHA-256 for each entry and exits non-zero on any
mismatch or missing file; rows the run never selected are reported `skipped`.
Commit the manifest next to your analysis; it is what makes the download
reproducible a year later.

Names are resolved against the manifest's own directory, and a name that points
outside it — `../sibling/file`, an absolute path, a symlink leading out — is
reported as `escaped` rather than quietly hashed, because a manifest is a
document someone can hand you. `--allow-escape` follows them; `--root` changes
what "inside" means.

When a source publishes no checksum (arXiv), verification degrades to a byte
count and `verified` is `null` rather than a false sense of safety.

## Supported sources

| Source | Target forms | Checksums |
| --- | --- | --- |
| Zenodo | DOI `10.5281/zenodo.N`, `zenodo:N`, record URL | md5 |
| arXiv | `arxiv:ID`, bare ID, abs/pdf URL | none (size only) |
| doi.org | any DOI, resolved then dispatched | depends |
| direct | any `http(s)` URL | headers, if present |

figshare (`figshare:N`, `10.6084/…`, article URLs) was removed in 0.2.0; it
answers with the conversion instead of a generic "cannot tell what this is".
See [docs/migration-0.2.md](docs/migration-0.2.md).

A CDN in front of a direct URL can answer 403 with a block page. Those are
retried twice with backoff, while a 403 carrying the application's own JSON is
terminal on the first answer; `--retry-403 N` changes the count.

## Politeness

Defaults: 3 parallel transfers, 2 requests/second, 4 retries with jittered
exponential backoff, `Retry-After` honoured up to 120 s. A block page is retried twice
(`--retry-403 N`); Zenodo and arXiv are donation-funded —
`--rate 1 -j 1` costs you minutes and costs them less.

Respect the license in the manifest. `--dry-run` prints it before you spend
bandwidth; `unknown` means *check before redistributing*.

## Adding a source

1. Read `acadfetch/sources/zenodo.py` — an adapter is `match(target) -> id | None`
   plus `resolve(client, target) -> Record`.
2. Fill `File(checksum=...)` whenever the API publishes one.
3. Never hardcode rate limits; take the shared `Client`.
4. Add a `match` test and, if possible, a fixture of the real API payload.

`scripts/probe_resume.py` interrupts a live transfer and resumes it — the only
honest way to test this code path.

## Getting involved

- **Stuck on something** → [docs/troubleshooting.md](docs/troubleshooting.md) first; it is short and indexed by symptom
- **Usage questions** → [Discussions](https://github.com/young-ski/acadfetch/discussions)
- **Bugs** → the bug template; say whether `verify` reports `MISMATCH`
- **Security** (bad bytes, path escapes, token leaks) → [`SECURITY.md`](SECURITY.md), privately
- **Adapters, tests, docs** → PRs welcome, read [`CONTRIBUTING.md`](CONTRIBUTING.md) first

## Known limits

- figshare was removed in 0.2.0; existing figshare manifests still verify, and
  new targets convert with one `--checksum` command ([docs/migration-0.2.md](docs/migration-0.2.md)).
- Tokens come from the environment only; no interactive `login` flow and no
  per-file token refresh.
- No search/pagination; you bring the target.
- arXiv PDFs are fetched by constructed URL, so a withdrawn ID gives a 404 after
  resolution.
- Windows ARM is untested.
- macOS is best effort: it is in CI but not release-blocking, and nobody has
  run it by hand yet.
- A source that publishes neither a size nor a checksum (bare `direct` URLs
  without a `Content-Length` on `HEAD`) can still be truncated silently by a
  server that lies about `Content-Length`, because HTTP gives no in-band way to
  notice the extra bytes.

## 中文

学术数据仓库的批量下载器：断点续传、全局限速、按仓库公布的校验和验证，并落
`manifest.json`（DOI、许可协议、每文件 SHA-256）供日后复现。

```bash
acadfetch fetch 10.5281/zenodo.123456 -o ./data
acadfetch verify ./data/manifest.json
```

MIT。

## Audit trail

How the 3.15 support claim was reached, including what it did **not** prove —
the free-threaded build, Windows ARM, and every place the probe ran short:
[docs/py315-windows-report.md](docs/py315-windows-report.md).

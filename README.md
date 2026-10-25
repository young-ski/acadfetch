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

Python 3.10+. No runtime dependencies.

```bash
pipx install acadfetch
# or, from a checkout
pipx install git+https://github.com/young-ski/acadfetch
python -m pip install -e .
```

## Usage

```bash
acadfetch fetch 10.5281/zenodo.123456          # a DOI
acadfetch fetch zenodo:123456                  # a record id
acadfetch fetch figshare:987654
acadfetch fetch arxiv:2401.12345
acadfetch fetch https://example.org/dataset.tar.gz

acadfetch fetch zenodo:123456 -n               # dry run: what would be fetched
acadfetch fetch zenodo:123456 --only raw.tar.gz
acadfetch fetch zenodo:123456 --max-size 2GB   # refuse anything bigger
acadfetch fetch zenodo:123456 -j 1 --rate 0.5  # be a good citizen
acadfetch manifest zenodo:123456 -o m.json     # metadata only, no bytes
acadfetch fetch zenodo:123456 --header "Authorization: Bearer $TOKEN"   # restricted record

acadfetch verify ./data/manifest.json          # re-hash a directory you downloaded
acadfetch sources                              # list adapters
```

Progress is one aggregate line for the whole run (`3 file(s)  12.4/44.1 MiB  1.8 MiB/s  ETA 0:17`),
redrawn in place on a TTY and written as occasional whole lines when stdout is a pipe.
`-q` turns it off.

Output defaults to `acadfetch-out/<source>-<doi-or-id>`. Files land in
`<name>.part` and are renamed only after the checksum matches, so an interrupted
run never leaves something that *looks* finished.

Exit codes: `0` success, `1` failure, `2` partial, `130` interrupted (rerun to resume).

## Tokens

`ZENODO_TOKEN` and `FIGSHARE_TOKEN` are read from the environment and sent only
to that repository, so a token never travels to a host that has no business
seeing it. `--header` still overrides, `--no-token` ignores the environment, and
the manifest records the variable name plus the last four characters only:

```bash
export ZENODO_TOKEN=...            # restricted records, higher rate limits
acadfetch fetch zenodo:123456      # prints: auth    : token from $ZENODO_TOKEN (…9f2c)
```

## Verification and the manifest

Every completed run writes `manifest.json`:

```json
{
  "schema": 1,
  "doi": "10.5281/zenodo.123456",
  "license": "cc-by-4.0",
  "files": [
    { "name": "raw.tar.gz", "size": 8123, "checksum": "md5:9f2c…",
      "sha256": "b41d…", "state": "downloaded", "verified": true }
  ]
}
```

`acadfetch verify` recomputes SHA-256 for each entry and exits non-zero on any
mismatch or missing file. Commit the manifest next to your analysis; it is what
makes the download reproducible a year later.

When a source publishes no checksum (arXiv), verification degrades to a byte
count and `verified` is `null` rather than a false sense of safety.

## Supported sources

| Source | Target forms | Checksums |
| --- | --- | --- |
| Zenodo | DOI `10.5281/zenodo.N`, `zenodo:N`, record URL | md5 |
| Figshare | DOI `10.6084/m9.figshare.N`, `figshare:N`, article URL | md5 |
| arXiv | `arxiv:ID`, bare ID, abs/pdf URL | none (size only) |
| doi.org | any DOI, resolved then dispatched | depends |
| direct | any `http(s)` URL | headers, if present |

Figshare's API sits behind a WAF that intermittently answers 403 to non-browser
clients (an AWS ELB block page, some networks always, some never). Those 403s
are retried twice with backoff, then the legacy per-file endpoint is tried;
a 403 that is the API's own permission error is still terminal. Downloads
themselves go to `ndownloader`, which the WAF leaves alone. The payload shapes
are pinned in `tests/fixtures/`.

If you know your network gets through, `--retry-403 0` skips the retries
entirely and makes every 403 final on the first answer.

## Politeness

Defaults: 3 parallel transfers, 2 requests/second, 4 retries with jittered
exponential backoff, `Retry-After` honoured up to 120 s. A WAF block page is
retried twice (`--retry-403 N`); Zenodo and arXiv are donation-funded —
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

## Known limits

- Tokens come from the environment only; no interactive `login` flow and no
  per-file token refresh.
- No search/pagination; you bring the target.
- arXiv PDFs are fetched by constructed URL, so a withdrawn ID gives a 404 after
  resolution.
- Windows ARM is untested.

## 中文

学术数据仓库的批量下载器：断点续传、全局限速、按仓库公布的校验和验证，并落
`manifest.json`（DOI、许可协议、每文件 SHA-256）供日后复现。

```bash
acadfetch fetch 10.5281/zenodo.123456 -o ./data
acadfetch verify ./data/manifest.json
```

MIT。

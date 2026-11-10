# `manifest.json` (schema 3)

The manifest is the artifact. It exists so that someone else — or you, two years
from now — can tell what was fetched, from where, under which license, and can
re-check that the bytes are the ones the repository published.

A manifest always describes **the record**, not the run. Every file the record
lists gets a row, whether or not this run asked for it.

## Top-level fields

| Field | Type | Meaning |
| --- | --- | --- |
| `schema` | int | `3`. Readers accept `1`, `2` and `3`. |
| `generated_at` | RFC 3339 | When the file was written, UTC. |
| `tool` | object | `{"name": "acadfetch", "version": …}`. |
| `source` | string | Adapter that produced the record (`zenodo`, `arxiv`, `direct`, …). |
| `target` | string | Exactly what the user typed. |
| `doi`, `title`, `license`, `publisher`, `version`, `landing` | string | Repository metadata, `""` when absent. |
| `selected_only` | bool | `true` when a run deliberately left part of the record alone (`--only`). |
| `remote_checked_at` | RFC 3339 \| null | When a remote re-check happened; `null` until `verify --against-remote` is run. |
| `auth` | object \| null | `{"env": "ZENODO_TOKEN", "hint": "…9f2c"}` — never the secret. |
| `files` | array | One row per file of the record. |

## File rows

| Field | Type | Meaning |
| --- | --- | --- |
| `name` | string | The name **on disk**, after sanitising and duplicate resolution. |
| `url` | string | The URL the record published. |
| `size` | int \| null | Published size, when there is one. |
| `checksum` | string \| null | `algo:hex`, as published. |
| `sha256` | string \| null | Hash of the bytes on disk, computed after the transfer. |
| `bytes` | int | Bytes written by this run. |
| `state` | string | See below. |
| `selected` | bool | Whether this run asked for the file. |
| `verified` | bool \| null | `true` matched, `false` mismatch, `null` nothing to check. |
| `verified_reason` | string | Machine-readable reason; see below. |

### States

| `state` | `verify` treats it as | Meaning |
| --- | --- | --- |
| `downloaded` | hash it | Bytes were fetched and renamed into place. |
| `skipped` | hash it | A verified copy was already on disk. |
| `failed` | hash it (hash may be `null`) | The transfer failed; `sha256` is `null`. |
| `missing` | **bad** | Selected, no result, no file. |
| `not-selected` | skipped | `--only` did not include it. |
| `planned` | skipped | Written by `acadfetch manifest`; nothing fetched yet. |

An unknown `state` is hashed rather than skipped: skipping is the exception, so a
future state does not quietly turn `verify` green.

### `verified_reason`

`matched-<algo>` · `mismatch` · `no-checksum-published` · `unparseable-checksum`
· `unchecked` · `transfer-failed` · `not-selected` · `planned`.

## What `verify` guarantees

- A name outside the manifest's directory is reported `escaped`, not hashed.
- `ok` means the file exists and its SHA-256 equals the recorded one.
- `skipped` rows do not fail the run; `--require-all` counts them as failures.
- An empty manifest is a failure, and `ok_run` is then `false`.

`verify` cannot prove the manifest itself is authentic — it is unsigned. What it
proves is that the bytes on disk are the bytes the manifest records.

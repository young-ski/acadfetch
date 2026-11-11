# The `--json` report

`fetch --json` and `verify --json` put exactly one JSON document on stdout and
move everything else to stderr, so `acadfetch … --json | jq` never has to filter
prose. A failure before any transfer still yields a document, with `error` set.

`report` is the document's schema version. Fields are **added**, never removed
or given a new meaning; `state` may gain values, so treat an unknown one as
non-terminal. Version `2` is current; `1` named the same things without
`selected`, `http_status` or the `remote*` fields, and 0.1.x emitted it.

## Envelope (every document)

| Field | Type | Meaning |
| --- | --- | --- |
| `tool` | object | `{"name": "acadfetch", "version": …}` |
| `report` | int | Schema version, `2`. |
| `command` | string | `fetch` or `verify`. |
| `started_at` | RFC 3339 | When the run began, UTC. |
| `error` | string \| null | Set when the run died before or outside the row list. |

## `fetch` document

Added to the envelope:

| Field | Type | Meaning |
| --- | --- | --- |
| `target` | string | What was typed, credentials redacted. |
| `source` | string | Adapter that resolved it. |
| `doi`, `license`, `landing` | string | Repository metadata. |
| `out` | string | Output directory. |
| `dry_run` | bool | `true` for `-n`: no bytes were moved. |
| `url_redacted` | bool | A credential query parameter was stripped; `--record-full-url` keeps it and sets this to `false`. |
| `files` | array | One row per selected file. |
| `summary` | object | `total`, `downloaded`, `skipped`, `failed`, and `ok` (`true` only on exit `0`). |
| `elapsed_s` | float | Wall clock, seconds. |

A dry run adds `disk` (`free`, `needed`, and `ok` when the plan does not fit),
and its `summary` has no `ok`.

### `fetch` file rows

| Field | Type | Meaning |
| --- | --- | --- |
| `name` | string | The name on disk. |
| `size` | int \| null | Published size. |
| `bytes` | int | Bytes written by this run. |
| `state` | string | `downloaded`, `skipped`, `failed` (or `planned` in a dry run). |
| `selected` | bool | Always `true` — a row exists only for a selected file. |
| `http_status` | int \| null | Final status, `null` when nothing was opened or in a dry run. |
| `final_url` | string \| null | Where the bytes came from after redirects, redacted. |
| `verified` | bool \| null | `true` matched, `false` mismatch, `null` nothing to check. |
| `verified_reason` | string | `matched-<algo>`, `mismatch`, `no-checksum-published`, `unparseable-checksum`, `unchecked`. |
| `resumed_from` | int | Bytes already on disk when this run started. |
| `part` | string \| null | Base name of the kept `.part`, when a failure left one. |
| `error` | string | Empty on success. |

## `verify` document

Added to the envelope:

| Field | Type | Meaning |
| --- | --- | --- |
| `manifest` | string | The path that was verified. |
| `root` | string | Directory the names resolve against. |
| `rows` | array | One row per manifest entry. |
| `summary` | object | `total`, `ok`, `bad`, `skipped`, `ok_run`. |
| `remote_checked_at` | RFC 3339 | Present only with `--against-remote`; also written to the manifest. |
| `remote` | object | Present only with `--against-remote`: counts per remote status. |

### `verify` rows

| Field | Type | Meaning |
| --- | --- | --- |
| `name` | string | Name from the manifest. |
| `status` | string | `ok`, `MISMATCH`, `missing`, `escaped`, `malformed`, `skipped`. |
| `state` | string | The manifest's state when the row was skipped (`not-selected`, `planned`). |
| `sha256` | string | Hash of the file on disk, when it was hashed. |
| `remote` | string | `remote_match`, `remote_mismatch`, `remote_no_checksum`, `remote_unavailable` — only with `--against-remote`. |
| `remote_detail` | string | Why a row could not be compared. |

`ok_run` is `false` when anything was `bad` and when the manifest lists no
files. A remote mismatch is `bad` even when the local row is `ok`; an
unreachable source is `bad` only with `--require-remote`.

## Exit codes in a document

Exit codes do not change under `--json`: `0` ok, `1` failure, `2` partial, `64`
bad usage, `130` interrupted. Run the command for the code, not the text.

## Redaction

Credential query parameters (`X-Amz-Signature`, `token`, …) are stripped from
`target`, `landing`, `final_url` and error messages. `url_redacted` says whether
that happened; `--record-full-url` disables it.

## Recipes

```bash
# every failed file
acadfetch fetch zenodo:123456 --json | jq -r '.files[] | select(.state=="failed") | .name'

# only what the source still vouches for
acadfetch verify data/manifest.json --against-remote --json \
  | jq -r '.rows[] | select(.remote != "remote_match") | .name'

# fail the shell on a partial run
acadfetch fetch … --json >/tmp/r.json || case $? in 2) echo partial >&2; exit 1;; esac
```

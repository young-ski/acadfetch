# Troubleshooting

Arranged by what you are looking at, not by cause. If nothing here matches, the
bug template asks for `-n` output and whether `verify` says `MISMATCH`.

## `HTTP 0` and a `…timed out` / `the link stalled` message

HTTP 0 is "no answer from the server", not a server error code. The transfer
stalled long enough to give up.

- The `.part` is kept -- rerun the same command and it resumes from where it
  stopped. Do not delete it.
- On a flaky link, slow it down so each transfer lives longer:
  `--rate 1 -j 1`.
- Behind a VPN or proxy, check that HTTPS through it works at all
  (`curl -sS -m 10 -o /dev/null -w '%{http_code}\n' https://zenodo.org`).

## `getaddrinfo failed`, or a host resolving to `0.0.0.0`

Name resolution, before any HTTP happens. Campus, hotel and airport resolvers
blackhole repository domains surprisingly often.

```bash
nslookup zenodo.org 1.1.1.1     # or 8.8.8.8
```

If the public resolver answers and yours does not, either fix DNS
(`--header` cannot help) or pin the address in `/etc/hosts`
(`%WINDIR%\System32\drivers\etc\hosts` on Windows) for the length of the job and
remove it afterwards -- a pinned address outlives a CDN change.

## A proxy, or TLS interception

Environment proxies are used automatically. When the proxy is only known per
run, pass it; when the environment's is wrong, turn it off:

```bash
acadfetch fetch … --proxy http://proxy.campus:3128
acadfetch fetch … --no-proxy
```

Corporate TLS inspection replaces the certificate chain: point `--ca-bundle` at
the inspecting CA's PEM, or the run fails with `CERTIFICATE_VERIFY_FAILED`.

A hard idle limit is `--timeout SECONDS` (default 60). Setting it below the
longest silence on the link makes transfers fail and keep their `.part` — a
retry then resumes.

## `403` with an HTML body

A CDN or WAF in front of the URL answered a block page. Two block-page retries
are the default; `--retry-403 0` skips them and fails at once,
`--retry-403 5` tries harder. A 403 whose body is the application's own JSON is
a permission answer and stays terminal either way.

## `figshare support was removed in 0.2.0`

Expected: the adapter is gone. Convert the target to the article's
`ndownloader` URL and pass `--checksum`; the recipe, and the fact that existing
figshare manifests still verify, is in
[docs/migration-0.2.md](migration-0.2.md).

## `server answered text/html for 'paper.pdf'`

A 200 that carried a block page, a login form or a captive portal. The markup is
refused instead of being saved as a PDF, and nothing is kept.

Open the target in a browser to see what it wants, then pass what it needs:

```bash
acadfetch fetch … --header "Cookie: session=…"
acadfetch fetch … --header "Authorization: Bearer …"
```

A captive portal also breaks every other host; log in and rerun.

## `needs 41 GiB but 12 GiB is free`

A pre-flight refusal, before a single byte is fetched. Only the remainder counts,
so an interrupted run needs far less than the record; if this fires on a resume,
something else filled the disk.

Free space, or point `-o` at another volume. `--no-fsync` does not help here (it
trades durability, not size). Emptying the recycle bin is often the whole fix.

## `verify` says `MISMATCH`

The file on disk is not the file the manifest recorded -- edited, truncated, or
corrupted by the filesystem. Delete the file and fetch again.

If the manifest is `schema 1`, two files from one record could share a name in it,
and the second row can report mismatch on correct bytes. Upgrade with a fresh
fetch: schema 2 records the name on disk. `verify` prints a note when it sees
schema 1.

## `verify` says `escaped`

A manifest is a document someone can hand you; a name that leaves the directory
is not hashed silently. `--root` says what "inside" means, `--allow-escape`
follows the name anyway.

## Long paths on Windows

`MAX_PATH` (260) applies unless long paths are enabled
(`HKLM\SYSTEM\CurrentControlSet\Control\FileSystem\LongPathsEnabled = 1`). Names
are shortened to 180 characters, but a deep `-o` can still overflow: fetch into a
shallower directory.

## A token is ignored, or a restricted record 403s

Tokens are read from `ZENODO_TOKEN` / `FIGSHARE_TOKEN` only, and only sent to that
repository's host. `--no-token` switches them off; `--header` overrides.
`acadfetch fetch … -n` prints which token it picked up, last four characters
only, so you can see whether the variable reached the process at all.

## No progress line

stdout is not a TTY (CI, `tee`, a wrapper script), so progress degrades to one
whole line every 5 s. `-q` turns it off entirely; the per-file result lines and
the exit code are unaffected.

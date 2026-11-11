# Moving off figshare (0.2.0)

figshare support was removed in 0.2.0. The API sits behind a WAF that answers a
block page to non-browser clients on some networks, and every retry costs a
request without fixing it. What is left is the file itself: an `ndownloader`
URL, which the WAF does not touch.

Nothing you already downloaded breaks. `acadfetch verify` reads names and
hashes, not adapters, so **an existing figshare manifest still verifies**.

## Convert a target

Old form → what to do:

| You used to type | Now |
| --- | --- |
| `acadfetch fetch figshare:32519553` | open <https://figshare.com/articles/32519553>, copy the download link |
| `acadfetch fetch 10.6084/m9.figshare.32519553` | same |
| `acadfetch fetch https://figshare.com/articles/…` | same |

The download link looks like
`https://ndownloader.figshare.com/files/<file_id>/<name>`. Pass it as a normal
URL, with the checksum the article page lists:

```bash
acadfetch fetch https://ndownloader.figshare.com/files/70600233/report.pdf \
    --checksum md5:bbde85208529e663141a0e8cb6fa9c4a
```

`--checksum` takes `md5:<hex>` or `sha256:<hex>`; a malformed value is refused
before any request. It wins over a checksum the source publishes, and the
manifest records `checksum_source: "cli"` so a reader can see where it came
from. The name segment of an `ndownloader` URL is free-form; the checksum is
what decides.

## What is lost, and what is not

- **Kept**: resumable transfer, the manifest, per-file SHA-256, `verify`, the
  license-independent checksum check.
- **Lost**: the API metadata — `doi`, `title`, `license`, `publisher` come back
  empty for a direct URL, and the record is one file per command instead of the
  whole article. Put the DOI in your notes; `manifest.landing` records the URL
  you actually fetched.

If a file has no published checksum, verification degrades to a byte count and
`verified` is `null`, exactly as for arXiv. `--checksum` is how you get the
strong form back.

## Why the error is not generic

`figshare:1`, `10.6084/…` and `figshare.com/articles/…` are still recognised.
They return this same recipe instead of "cannot tell what that is", and
`acadfetch sources` keeps one line pointing here. The adapter is gone; the
target is not unknown.

# Fitting acadfetch into a pipeline

acadfetch is the fetch step, not the whole job. Three ways to keep it there
without copying prose out of a terminal.

## aria2 / rclone

`-w/--urls` writes an aria2 input list — URL, `out=`, `dir=` — and transfers
nothing:

```bash
acadfetch fetch 10.5281/zenodo.23612969 -w - --glob '*.csv' | aria2c -i -
```

The manifest is not written on this path (aria2 did the transfer), so run
`acadfetch manifest <target> -o data/manifest.json` if you still want one.

## Make

```make
data/manifest.json:
	acadfetch fetch 10.5281/zenodo.23612969 -o data
	acadfetch verify data/manifest.json
```

A failed transfer keeps its `.part`; rerunning the same rule resumes.

## GitHub Actions

```yaml
- name: Fetch the dataset
  run: |
    uvx acadfetch fetch 10.5281/zenodo.23612969 -o data
    uvx acadfetch verify data/manifest.json --against-remote
```

`--against-remote` is worth having in CI: it fails when the repository's
published checksums stop matching the manifest, which is also what a rewritten
manifest looks like. Add `ZENODO_TOKEN` from a secret for restricted records.

## Snakemake

There is a storage plugin, `snakemake-storage-plugin-acadfetch` (in this
repository under `snakemake-storage-plugin-acadfetch/`). A rule names a file URL —
the form `acadfetch fetch <target> -w -` prints — and gets acadfetch's transport:
one token bucket, `Retry-After`, retried 5xx and block pages, a resumed `.part`.

```snakemake
rule fetch:
    input:
        storage.acadfetch("https://zenodo.org/records/123456/files/raw.tar.gz")
    output:
        "raw.tar.gz"
    shell:
        "cp {input} {output}"
```

```bash
pip install snakemake-storage-plugin-acadfetch
```

`--storage-acadfetch-rate`, `-retries`, `-timeout` and `-header` tune it the way
the CLI flags of the same name do.

For a whole record with a manifest beside it, a rule that shells out is still the
simpler thing:

```python
rule dataset:
    output: directory("data")
    run:
        shell("acadfetch fetch 10.5281/zenodo.23612969 -o {output}")
        shell("acadfetch verify {output}/manifest.json")
```

Pooch is covered below; a BagIt export is not implemented — the manifest already
carries what it would need, so it is a thin wrapper away.

## Pooch

`pooch` handles caching, hashing and moving a file into place, but its downloader
issues one flat GET: a link that drops at 80% costs the whole file. `acadfetch`
ships a pooch downloader that supplies only the transport — the same client the
CLI uses, so `Retry-After` is honoured, a 5xx or a WAF block page is retried, the
`.part` is resumed instead of restarted, and a credential header is dropped when a
redirect leaves the origin.

```python
import pooch
from acadfetch.pooch import downloader

path = pooch.retrieve(
    "https://zenodo.org/records/123456/files/raw.tar.gz",
    known_hash="md5:9f2c…",
    downloader=downloader,
)
```

pooch still does the hashing and the move, so this cannot weaken verification — it
can only make the bytes arrive. Configure it like the CLI with
`Downloader(rate=1, retries=4)`, and reach a restricted record with
`Downloader(headers={"Authorization": "Bearer …"})`.

Two limits come from how pooch drives a downloader, and are worth knowing before
you rely on it: pooch hands each attempt a **fresh temporary file**, so a partial
is resumed within one attempt and not across pooch's own retries; and
`progressbar=True` is implemented inside pooch's downloader, so a transfer through
this one is silent.

## Politeness

A shared runner and a donation-funded repository both prefer `--rate 1 -j 1`.
The default is 2 requests/second and 3 transfers.

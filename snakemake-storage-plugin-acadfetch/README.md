# snakemake-storage-plugin-acadfetch

A [Snakemake](https://snakemake.github.io/) storage plugin that fetches file URLs
through [acadfetch](https://github.com/young-ski/acadfetch).

Snakemake's own `http` plugin issues one flat GET per file, so a link that drops
at 80% costs the whole transfer. This one routes every request through
acadfetch's client: one process-wide token bucket, `Retry-After` respected, 5xx
and WAF block pages retried, and a `.part` resumed with `Range`/`If-Range`
instead of restarted.

## Install

```bash
pip install "git+https://github.com/young-ski/acadfetch.git#subdirectory=snakemake-storage-plugin-acadfetch"
```

The intended PyPI name is `snakemake-storage-plugin-acadfetch`; until that release
exists, install from the checkout as above.

## Use

A query is a file URL — exactly the form `acadfetch fetch <target> -w -` prints
for every file of a record:

```snakemake
rule fetch:
    input:
        storage.acadfetch("https://zenodo.org/records/123456/files/raw.tar.gz")
    output:
        "raw.tar.gz"
    shell:
        "cp {input} {output}"
```

Get those URLs from a DOI or a record id without hand-copying anything:

```bash
acadfetch fetch 10.5281/zenodo.123456 -w - | head -1
```

## Settings

Each appears on the Snakemake CLI as `--storage-acadfetch-<name>`:

| Setting | Meaning |
| --- | --- |
| `rate` | requests per second, global (default 2) |
| `retries` | retries per transfer on 5xx/429/network errors (default 4) |
| `timeout` | per-request timeout in seconds (default 60) |
| `header` | extra request header as `Name: value`, for a restricted record |

```bash
snakemake --storage-acadfetch-rate 1 --storage-acadfetch-header "Authorization: Bearer $TOKEN"
```

## What it does not do

A query is one file. Listing a record's files is what `-w/--urls` is for; doing it
here would add a network round trip per rule and hide which file the rule depends
on.

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

No storage plugin yet; a rule that shells out is enough, and `-w` avoids
re-downloading through another process:

```python
rule dataset:
    output: directory("data")
    run:
        shell("acadfetch fetch 10.5281/zenodo.23612969 -o {output}")
        shell("acadfetch verify {output}/manifest.json")
```

Pooch and BagIt exports are not implemented either; the manifest already carries
what both would need, so they are a thin wrapper away.

## Politeness

A shared runner and a donation-funded repository both prefer `--rate 1 -j 1`.
The default is 2 requests/second and 3 transfers.

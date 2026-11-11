# 30-second tour

Every command below is real and the output is what it produces. On a normal
connection this is about half a minute, most of it download.

```console
$ uvx acadfetch fetch 10.5281/zenodo.23612969 -n
title   : Dinler Tarihi Alanında Güncel Konular ve Akademik Değerlendirmeler
doi     : 10.5281/zenodo.23612969
license : cc-by-4.0
files   : 1, 1.6 MiB
landing : https://zenodo.org/records/23612969
would write to acadfetch-out/zenodo-10.5281_zenodo.23612969
disk    : 1.6 MiB needed, 54.0 GiB free
  would fetch Dinler Tarihi Alanında Güncel Konular ve Akademik Değerlendirmeler.pdf (1.6 MiB)

$ uvx acadfetch fetch 10.5281/zenodo.23612969
…
ok   Dinler Tarihi Alanında Güncel Konular ve Akademik Değerlendirmeler.pdf 1.6 MiB
1/1 file(s) in acadfetch-out/zenodo-10.5281_zenodo.23612969

$ uvx acadfetch verify acadfetch-out/zenodo-10.5281_zenodo.23612969/manifest.json
       ok Dinler Tarihi Alanında Güncel Konular ve Akademik Değerlendirmeler.pdf

1/1 ok
```

The manifest next to those bytes records the DOI, the licence and the SHA-256 of
every file. That is what `verify` reads, and what someone else needs in two
years.

## What to try next

```bash
# one file out of a record, by pattern
acadfetch fetch 10.5281/zenodo.23612969 --glob '*.pdf' -n

# hand the transfer to aria2
acadfetch fetch 10.5281/zenodo.23612969 -w - | aria2c -i -

# ask the source again whether the manifest still matches
acadfetch verify ./data/manifest.json --against-remote

# copy a citation, tool and data separately
acadfetch --cite
```

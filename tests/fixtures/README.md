# test fixtures

Everything here is a recorded response, not a hand-written guess.

`figshare_403_block.html` is the verbatim body `api.figshare.com` answered on
2026-10-24 from a network the WAF dislikes — the AWS ELB default block page,
`Content-Type: text/html`. A browser User-Agent does not help; it only appends
the "padding to disable MSIE and Chrome friendly error page" comments. This is
the case that must be retried (`--retry-403`).

`figshare_403_forbidden.json` is the shape of the API's own permission error
(`{"message": "Forbidden"}`), which must stay terminal so a genuinely
restricted article fails fast instead of burning retries.

`figshare_article.json` was captured on 2026-10-25 from
`https://api.figshare.com/v2/articles/32519553`, a real public record, over a
network (Japan) the WAF lets through. `figshare_file.pdf` is the file that
article publishes, downloaded from the `download_url` in the payload; its bytes
hash to the `computed_md5` recorded there (193 410 bytes,
`bbde85208529e663141a0e8cb6fa9c4a`). `test_figshare_payload_matches_the_stored_bytes`
fails if either side is swapped, and the downloader suite hashes these real
bytes so the verify path is proven against a published checksum, not a
synthetic string.

Two deviations from the raw capture, both made in tests rather than in the
file, so the fixture stays verbatim:

* the payload is pretty-printed and its long `keywords`/`categories` lists are
  trimmed;
* the article publishes a single file, so the no-`download_url` branch (legacy
  endpoint as primary) is covered by appending a synthetic entry inside
  `tests/test_sources.py`.

Re-capture with:

```bash
python -c "import urllib.request,pathlib;r=urllib.request.Request('https://api.figshare.com/v2/articles/32519553',headers={'User-Agent':'acadfetch/0.1.0'});pathlib.Path('tests/fixtures/figshare_article.json').write_bytes(urllib.request.urlopen(r,timeout=60).read())"
```

If the article changes, update `figshare_file.pdf` and the md5 in
`tests/test_downloader.py` together with it.

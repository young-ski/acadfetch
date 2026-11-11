# test fixtures

Everything here is a recorded response, not a hand-written guess.

Two files outlived the figshare adapter (removed in 0.2.0) because they are the
generic shapes:

`figshare_403_block.html` — the verbatim body a WAF/CDN answered on 2026-10-24,
the AWS ELB default block page, `Content-Type: text/html`. A browser User-Agent
does not help; it only appends "padding to disable MSIE and Chrome friendly
error page" comments. This is the case that must be retried (`--retry-403`).

`figshare_403_forbidden.json` — the shape of an application's own permission
error (`{"message": "Forbidden"}`), which must stay terminal so a genuinely
restricted file fails fast instead of burning retries.

`figshare_403_block.html` and `figshare_403_forbidden.json` are used by
`tests/test_client.py` and `tests/test_downloader.py` through the conftest
server; the names keep the provenance.

`figshare_file.pdf` is a real 193 410-byte PDF whose md5 is
`bbde85208529e663141a0e8cb6fa9c4a`. It is the only real-bytes fixture, so the
verify path is proven against a published checksum rather than a synthetic
string; `tests/test_downloader.py` holds the hash.

`figshare_article.json` is the API payload for the article that published that
file, captured on 2026-10-25 from
`https://api.figshare.com/v2/articles/32519553` (a network in Japan the WAF let
through). The adapter is gone, so only `test_figshare_payload_matches_the_stored_bytes`
in `tests/test_sources.py` still reads it, as a guard that the PDF and the md5
have not drifted apart.

Two deviations from the raw capture, both made in tests rather than in the file:

* the payload is pretty-printed and its long `keywords`/`categories` lists are
  trimmed;
* the article publishes a single file, so the legacy-endpoint branch is covered
  by appending a synthetic entry inside `tests/test_sources.py`.

Re-capture with:

```bash
python -c "import urllib.request,pathlib;r=urllib.request.Request('https://api.figshare.com/v2/articles/32519553',headers={'User-Agent':'acadfetch/0.2.0'});pathlib.Path('tests/fixtures/figshare_article.json').write_bytes(urllib.request.urlopen(r,timeout=60).read())"
```

If the article changes, update `figshare_file.pdf` and the md5 in
`tests/test_downloader.py` together with it.

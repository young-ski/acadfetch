# test fixtures

`figshare_403_block.html` is the verbatim body `api.figshare.com` answered on
2026-10-24 from a network the WAF dislikes — the AWS ELB default block page,
`Content-Type: text/html`. A browser User-Agent does not help; it only appends
the "padding to disable MSIE and Chrome friendly error page" comments. This is
the case that must be retried.

`figshare_403_forbidden.json` is the shape of the API's own permission error
(`{"message": "Forbidden"}`), which must stay terminal so a genuinely
restricted article fails fast instead of burning retries.

`figshare_article.json` is a v2 article payload cut down to two files. It was
**reconstructed** to the documented v2 schema rather than captured: at the time
of writing every `api.figshare.com` request from this network answered the
block page above, so there was no 200 to record. It deliberately keeps

* a file with `download_url` **and** `computed_md5` (the normal case),
* a file with an `id` but no `download_url` (must fall back to the legacy
  `/v2/articles/{id}/files/{file_id}/download` endpoint).

The file contents that the two `computed_md5` values describe are in
`tests/test_sources.py`; if these fixtures are ever re-captured from a live
article, replace those values together with the hashes.

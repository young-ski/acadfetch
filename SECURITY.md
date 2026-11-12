# Security policy

## What is in scope

`acadfetch` treats repository metadata as hostile and promises certain things
about bytes on disk. These are the interesting bugs:

- a crafted filename or manifest that writes outside the output directory;
- a transfer that reaches a "complete" file whose bytes are wrong (lying
  `Content-Length`, ignored `Range`, unvalidated `206` offset);
- a token sent to a host that has no business seeing it -- including on a
  redirect hop, or added to a presigned URL that already carries its own
  credentials -- or a token leaking into `manifest.json` / stdout beyond the last
  four characters;
- anything that makes `verify` report `ok` for bytes that do not match.

Out of scope: vulnerabilities in the repositories themselves, missing HTTP
security headers on their side, and the fact that a manifest is unsigned (see
below).

## Supported versions

| Version | Supported |
| --- | --- |
| 0.6.x | yes |
| earlier | no — `pip install -U acadfetch` first |

Only the current minor receives security fixes. A report against an older
version is taken seriously, but the fix lands in a new release of the current
line.

Releases are cut on demand, not on a schedule. Security fixes get a patch
version and a `### Security` entry in `CHANGELOG.md`.

## Reporting

Use GitHub's private channel: **Security → Advisories → Report a vulnerability**
on <https://github.com/young-ski/acadfetch/security/advisories/new>. Please do
not open a public issue first.

Expect an acknowledgement within 72 hours. If the report holds up, the fix, the
advisory and the release come together, and you are credited unless you ask
otherwise.

## Known limitation worth knowing before you report it

`manifest.json` is not signed. `acadfetch verify` proves that the bytes on disk
match the manifest; it cannot prove the manifest itself is authentic. Someone
who can rewrite both the files and the manifest will pass verification.

What closes as much of that gap as can be closed is `verify --against-remote`
(shipped in 0.3.0): it asks the source again and compares what it publishes now,
so a rewritten manifest stops agreeing with the repository even though the bytes
still agree with the manifest. It is off by default because it makes network
requests; see [docs/manifest.md](docs/manifest.md#remote-re-check). It is still
not a signature — a compromise of the repository itself defeats it.

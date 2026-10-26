# Pull requests

Thanks for this. Two things keep the review short: one concern per PR, and the
invariant checklist below.

## What changed

<!-- one paragraph; link the issue it closes with `closes #N` -->

## Invariant checklist

Tick what your change touches. Untick nothing you have not checked.

- [ ] **No new runtime dependency** (or: the argument for one is in the description)
- [ ] Every outbound request still goes through `acadfetch.client.Client`
- [ ] Bytes still land only in `.part`, renamed only after verification
- [ ] Anything from repository metadata still goes through `naming.sanitize` /
      `naming.unique`
- [ ] Tokens still only travel to their own source; nothing but the env var name
      and the last four characters reaches the manifest
- [ ] Progress can still not fail a download
- [ ] A source with no published checksum still reports `verified=null`

## Tests

- [ ] `python -m pytest -q` passes offline
- [ ] `ruff check . && ruff format --check .` clean
- [ ] New behaviour has a test that fails without the change

<!-- For adapter PRs only -->

- [ ] `match()` is narrow enough not to shadow another adapter
- [ ] A real API payload is in `tests/fixtures/` (trimmed is fine)
- [ ] The checksum field used is the repository-computed one, if there is a choice

## Breaking change

- [ ] No
- [ ] Yes — described here, with a `CHANGELOG.md` entry and a version bump

## How you verified it

<!-- commands you ran; a real transfer beats a mock where possible -->

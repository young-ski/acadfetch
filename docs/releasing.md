# Releasing

On demand, not on a schedule. One maintainer, so the checklist exists to be
followed at 23:00 when memory is worst.

## 1. Decide the version

Semantic Versioning, pre-1.0: a minor bump may break a flag, and a changed
manifest field meaning is minor even though the shape looks additive. Patch is
for fixes that change no contract.

## 2. Bump both places

`pyproject.toml` (`version`) and `acadfetch/consts.py` (`__version__`).
`tests/test_packaging.py` fails if they disagree, and the release guard fails if
the tag disagrees with `pyproject.toml`.

## 3. Promote the changelog

Move the `[Unreleased]` entries under `## [x.y.z] - YYYY-MM-DD`. Leave an empty
`## [Unreleased]` behind. A `### Security` heading is only for advisories.

## 4. Run the gates locally

```bash
ruff check . && ruff format --check .
pytest -q --cov=acadfetch --cov-fail-under=85
python -X dev -W error -m pytest -q
python -m build && python -m twine check dist/*
```

## 5. Tag on main, and push the tag alone

```bash
git switch main && git pull
git tag -a v0.1.2 -m "0.1.2"
git push origin v0.1.2
```

The tag must point at a commit that is an ancestor of `main`, and the release
guard enforces exactly that. Do not tag a branch tip.

## 6. Watch the run

`gates` → `guard` → `publish` → `smoke`. The `smoke` job installs the published
version from PyPI and resolves a real record, so a broken wheel is a red run
rather than a bug report.

Two tags pushed together serialise through the `release` concurrency group; the
second waits rather than racing the filename.

## 7. After it is up

- Check the smoke job and the PyPI page.
- If the wheel is bad, **yank** it (PyPI → project → Manage → Releases → Yank)
  rather than deleting: `pip install acadfetch==x.y.z` then resolves to nothing
  instead of installing something broken.
- If only the metadata is wrong, a `.postN` release is cheaper than a yank.
- Fix forward. A yanked version stays in the changelog with a note saying why.

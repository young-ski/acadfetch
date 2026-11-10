from __future__ import annotations

import pytest

from acadfetch.errors import TargetError
from acadfetch.sources import registry, removed


@pytest.mark.parametrize(
    "target",
    [
        "figshare:32519553",
        "10.6084/m9.figshare.32519553",
        "https://figshare.com/articles/Some_title/32519553",
        "https://www.figshare.com/articles/32519553",
    ],
)
def test_figshare_targets_get_a_migration_path(target):
    text = removed.explain(target)
    assert text and "removed in 0.2.0" in text
    assert "ndownloader.figshare.com/files/" in text
    assert "--checksum md5:<computed_md5>" in text


def test_the_record_id_is_carried_into_the_article_url():
    assert "articles/32519553" in removed.explain("figshare:32519553")


@pytest.mark.parametrize(
    "target",
    ["10.5281/zenodo.1", "zenodo:1", "arxiv:2401.12345", "https://example.org/a.csv"],
)
def test_other_targets_are_not_claimed(target):
    assert removed.explain(target) is None


def test_the_registry_speaks_before_any_request():
    """No client at all: the tombstone must not need the network."""
    with pytest.raises(TargetError) as caught:
        registry.resolve(None, "figshare:32519553")  # type: ignore[arg-type]
    assert "removed in 0.2.0" in str(caught.value)


def test_a_doi_org_url_is_unwrapped_first():
    with pytest.raises(TargetError, match="removed in 0.2.0"):
        registry.resolve(None, "https://doi.org/10.6084/m9.figshare.32519553")  # type: ignore[arg-type]


def test_sources_note_names_the_replacement():
    assert removed.SOURCES_NOTE.startswith("figshare")
    assert "ndownloader" in removed.SOURCES_NOTE

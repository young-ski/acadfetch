from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from acadfetch import verify
from acadfetch.errors import SourceError, TargetError
from acadfetch.sources import arxiv, direct, doi, figshare, registry, zenodo

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text("utf-8"))


class FakeClient:
    """Minimal stand-in for Client: canned JSON per URL."""

    def __init__(self, payloads):
        self.payloads = payloads
        self.seen = []

    def get_json(self, url, headers=None):
        self.seen.append(url)
        for fragment, payload in self.payloads.items():
            if fragment in url:
                return payload
        raise KeyError(url)

    def head(self, url):
        return {}


ZENODO = {
    "id": 42,
    "doi": "10.5281/zenodo.42",
    "is_latest": True,
    "links": {"self_html": "https://zenodo.org/records/42"},
    "metadata": {
        "title": "Test record",
        "license": {"id": "cc-by-4.0"},
        "publisher": "Zenodo",
    },
    "files": [
        {
            "key": "a.csv",
            "size": 12,
            "checksum": "md5:" + "0" * 32,
            "links": {"self": "https://zenodo.org/api/records/42/files/a.csv/content"},
        },
    ],
}


@pytest.mark.parametrize(
    "adapter,target,expected",
    [
        (zenodo, "zenodo:42", "42"),
        (zenodo, "10.5281/zenodo.42", "42"),
        (zenodo, "https://zenodo.org/records/42", "42"),
        (zenodo, "https://zenodo.org/doi/10.5281/zenodo.42", "42"),
        (figshare, "figshare:9", "9"),
        (figshare, "10.6084/m9.figshare.9", "9"),
        (arxiv, "2401.12345", "2401.12345"),
        (arxiv, "arxiv:2401.12345v2", "2401.12345v2"),
        (arxiv, "https://arxiv.org/abs/2401.12345", "2401.12345"),
    ],
)
def test_adapters_match(adapter, target, expected):
    assert adapter.match(target) == expected


@pytest.mark.parametrize(
    "adapter,target",
    [
        (zenodo, "zenodo:abc"),
        (figshare, "figshare:x"),
        (arxiv, "not-an-id"),
        (direct, "zenodo:42"),
    ],
)
def test_adapters_reject(adapter, target):
    assert adapter.match(target) is None


def test_registry_rejects_unknown():
    with pytest.raises(TargetError):
        registry.resolve(FakeClient({}), "some nonsense")


def test_registry_rejects_empty():
    with pytest.raises(TargetError):
        registry.resolve(FakeClient({}), "   ")


def test_registry_bare_digits_are_zenodo():
    record = registry.resolve(FakeClient({"records/42": ZENODO}), "42")
    assert record.source == "zenodo"
    assert record.doi == "10.5281/zenodo.42"


def test_zenodo_resolve_maps_fields():
    record = zenodo.resolve(FakeClient({"records/42": ZENODO}), "zenodo:42")
    assert record.files[0].name == "a.csv"
    assert record.files[0].checksum.startswith("md5:")
    assert record.license == "cc-by-4.0"
    assert record.publisher == "Zenodo"


def test_zenodo_restricted_files_raise():
    payload = dict(ZENODO, access={"files": "restricted"})
    with pytest.raises(Exception, match="restricted"):
        zenodo.resolve(FakeClient({"records/42": payload}), "zenodo:42")


def test_doi_dispatch_goes_through_url_adapter():
    client = FakeClient(
        {
            "doi.org/api/handles": {
                "responseCode": 1,
                "values": [{"type": "URL", "data": {"value": "https://zenodo.org/records/42"}}],
            },
            "records/42": ZENODO,
        }
    )
    record = registry.resolve(client, "10.5281/zenodo.42")
    assert record.source == "zenodo"


def test_doi_url_is_unwrapped_to_a_doi():
    """doi.org landing URLs must not be handed to the direct adapter."""
    client = FakeClient(
        {
            "doi.org/api/handles": {
                "responseCode": 1,
                "values": [
                    {"type": "URL", "data": {"value": "https://zenodo.org/doi/10.5281/zenodo.42"}}
                ],
            },
            "records/42": ZENODO,
        }
    )
    record = registry.resolve(client, "https://doi.org/10.5281/zenodo.42")
    assert record.source == "zenodo"
    assert record.license == "cc-by-4.0"


def test_doi_pointing_at_a_landing_page_still_dispatches():
    client = FakeClient(
        {
            "doi.org/api/handles": {
                "responseCode": 1,
                "values": [
                    {"type": "URL", "data": {"value": "https://zenodo.org/doi/10.5281/zenodo.42"}}
                ],
            },
            "records/42": ZENODO,
        }
    )
    assert registry.resolve(client, "10.5281/zenodo.42").source == "zenodo"


def test_unregistered_doi_raises():
    client = FakeClient({"doi.org/api/handles": {"responseCode": 100}})
    with pytest.raises(TargetError, match="not registered"):
        doi.resolve(client, "10.9999/nope")


# -- figshare, against the recorded payload -------------------------------

SAMPLE = {
    "counts.csv": b"site,taxon,count\nA,Ephemeroptera,12\nB,Trichoptera,7\n",
    "method notes.txt": b"kick-net, 500um\n",
}


def figshare_record() -> dict:
    return figshare.resolve(
        FakeClient({"articles/13054873": fixture("figshare_article.json")}), "figshare:13054873"
    )


def test_figshare_resolve_maps_the_payload():
    record = figshare_record()
    assert [f.name for f in record.files] == ["counts.csv", "method notes.txt"]
    assert record.source == "figshare"
    assert record.title.startswith("Example dataset")
    assert record.doi == "10.6084/m9.figshare.13054873.v2"
    assert record.license == "CC BY 4.0"
    assert record.landing.endswith("/13054873")
    assert record.total_bytes == 68


def test_figshare_prefers_the_download_url_and_keeps_the_legacy_one():
    csv, notes = figshare_record().files
    assert csv.url == "https://ndownloader.figshare.com/files/24641751"
    assert csv.fallbacks == [
        "https://api.figshare.com/v2/articles/13054873/files/24641751/download"
    ]
    # Without a download_url the legacy endpoint is the primary, not a fallback.
    assert notes.url == "https://api.figshare.com/v2/articles/13054873/files/24641752/download"
    assert notes.fallbacks == []


def test_figshare_uses_computed_md5_not_the_supplied_one():
    payload = fixture("figshare_article.json")
    payload["files"][0]["supplied_md5"] = "0" * 32  # what the uploader claimed
    record = figshare.resolve(FakeClient({"articles/13054873": payload}), "figshare:13054873")
    algo, expected = verify.parse(record.files[0].checksum)
    assert algo == "md5"
    assert hashlib.md5(SAMPLE["counts.csv"]).hexdigest() == expected


def test_figshare_fixture_checksums_match_its_bytes():
    for item in figshare_record().files:
        _, expected = verify.parse(item.checksum)
        assert hashlib.md5(SAMPLE[item.name]).hexdigest() == expected
        assert item.size == len(SAMPLE[item.name])


def test_figshare_files_without_url_or_id_are_skipped():
    payload = fixture("figshare_article.json")
    payload["files"].append({"name": "ghost.txt"})
    record = figshare.resolve(FakeClient({"articles/13054873": payload}), "figshare:13054873")
    assert [f.name for f in record.files] == ["counts.csv", "method notes.txt"]


def test_figshare_without_files_raises():
    payload = fixture("figshare_article.json")
    payload["files"] = []
    with pytest.raises(SourceError, match="no downloadable files"):
        figshare.resolve(FakeClient({"articles/13054873": payload}), "figshare:13054873")

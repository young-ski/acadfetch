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


# -- figshare, against the captured payload -------------------------------

# Captured verbatim from api.figshare.com on 2026-10-25 (article 32519553, a real
# public record); the bytes in figshare_file.pdf are the real file and hash to
# the md5 the payload publishes.
ARTICLE = "32519553"
NAME = "3_Summary of Findings Across Studies_20261024_v2.pdf"
MD5 = "bbde85208529e663141a0e8cb6fa9c4a"
SIZE = 193410
PDF = (FIXTURES / "figshare_file.pdf").read_bytes()


def figshare_record(payload: dict | None = None) -> dict:
    payload = payload if payload is not None else fixture("figshare_article.json")
    return figshare.resolve(FakeClient({f"articles/{ARTICLE}": payload}), f"figshare:{ARTICLE}")


def test_figshare_resolve_maps_the_payload():
    record = figshare_record()
    assert [f.name for f in record.files] == [NAME]
    assert record.source == "figshare"
    assert record.title.startswith("3_Summary of Main Findings")
    assert record.doi == "10.6084/m9.figshare.32519553.v10"
    assert record.license == "CC BY 4.0"
    assert record.publisher == "Figshare"
    assert record.landing.endswith("/32519553")
    assert record.total_bytes == SIZE
    assert record.files[0].size == SIZE


def test_figshare_prefers_the_download_url_and_keeps_the_legacy_one():
    (only,) = figshare_record().files
    assert only.url == "https://ndownloader.figshare.com/files/70600233"
    assert only.fallbacks == [
        "https://api.figshare.com/v2/articles/32519553/files/70600233/download"
    ]


def test_figshare_without_a_download_url_uses_the_legacy_endpoint():
    """The payload field is optional, so the fallback path is exercised separately."""
    payload = fixture("figshare_article.json")
    payload["files"].append({"id": 70600234, "name": "notes.txt", "size": 12})
    record = figshare.resolve(FakeClient({f"articles/{ARTICLE}": payload}), f"figshare:{ARTICLE}")
    notes = record.files[1]
    assert notes.url == "https://api.figshare.com/v2/articles/32519553/files/70600234/download"
    assert notes.fallbacks == []
    assert notes.checksum is None


def test_figshare_uses_computed_md5_not_the_supplied_one():
    payload = fixture("figshare_article.json")
    payload["files"][0]["supplied_md5"] = "0" * 32  # what the uploader claimed
    algo, expected = verify.parse(figshare_record(payload).files[0].checksum)
    assert algo == "md5"
    assert expected == MD5


def test_figshare_payload_matches_the_stored_bytes():
    """Guards the fixture itself: if one side is swapped, this fails."""
    (only,) = figshare_record().files
    _, expected = verify.parse(only.checksum)
    assert hashlib.md5(PDF).hexdigest() == expected
    assert only.size == len(PDF) == SIZE


def test_figshare_files_without_url_or_id_are_skipped():
    payload = fixture("figshare_article.json")
    payload["files"].append({"name": "ghost.txt"})
    assert [f.name for f in figshare_record(payload).files] == [NAME]


def test_figshare_without_files_raises():
    payload = fixture("figshare_article.json")
    payload["files"] = []
    with pytest.raises(SourceError, match="no downloadable files"):
        figshare_record(payload)


# -- arXiv resolve (no fixture covered it before) -------------------------

ATOM = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>https://arxiv.org/abs/2401.12345v2</id>
    <title>Distributionally
      Robust Receive Combining</title>
  </entry>
</feed>
"""


class OpenClient(FakeClient):
    """A FakeClient that can also serve a body, and records that it was closed."""

    def __init__(self, body: bytes):
        super().__init__({})
        self.body = body
        self.closed = False
        self.url = ""

    def open(self, url, headers=None, attempts=None):
        self.url = url
        outer = self

        class Resp:
            def read(self, _n=None):
                return outer.body

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                outer.closed = True
                return False

        return Resp()


def test_arxiv_resolve_maps_the_entry_and_closes_the_response():
    client = OpenClient(ATOM)
    record = arxiv.resolve(client, "arxiv:2401.12345v2")
    assert record.source == "arxiv"
    assert record.title == "Distributionally Robust Receive Combining"
    assert record.version == "2"
    assert record.files[0].url == "https://arxiv.org/pdf/2401.12345v2"
    assert record.files[0].size is None
    assert record.files[0].checksum is None
    assert record.doi == "10.48550/arXiv.2401.12345"
    assert client.closed is True


def test_arxiv_asks_over_https():
    client = OpenClient(ATOM)
    arxiv.resolve(client, "arxiv:2401.12345")
    assert client.url.startswith("https://export.arxiv.org/api/query")

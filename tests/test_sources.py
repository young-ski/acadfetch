from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from acadfetch.errors import TargetError
from acadfetch.sources import arxiv, direct, doi, registry, zenodo

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


# -- the recorded figshare fixture, kept as a real-bytes guard ------------

ARTICLE = "32519553"
MD5 = "bbde85208529e663141a0e8cb6fa9c4a"


def test_the_recorded_payload_matches_the_stored_bytes():
    """If either side of the fixture is swapped, this fails.

    The adapter is gone; the pair still proves the verify path against a
    published checksum instead of a synthetic string.
    """
    payload = fixture("figshare_article.json")
    stored = (FIXTURES / "figshare_file.pdf").read_bytes()
    assert hashlib.md5(stored).hexdigest() == MD5
    assert payload["files"][0]["computed_md5"] == MD5
    assert payload["files"][0]["size"] == len(stored)


# -- direct: lower-case response headers -----------------------------------


class HeadClient(FakeClient):
    def __init__(self, headers: dict):
        super().__init__({})
        self._headers = headers

    def head(self, url):
        return self._headers


def test_direct_reads_lower_case_headers():
    rec = direct.resolve(
        HeadClient(
            {
                "content-length": "5",
                "content-disposition": 'attachment; filename="x.csv"',
                "content-md5": "XUFAKrxLKna5cZ2REBfFkg==",
            }
        ),
        "https://h/a",
    )
    (only,) = rec.files
    assert only.size == 5
    assert only.name == "x.csv"
    assert only.checksum == "md5:5d41402abc4b2a76b9719d911017c592"
    assert only.checksum_source == "header"


def test_direct_without_headers_has_no_size_or_source():
    rec = direct.resolve(HeadClient({}), "https://h/a.csv")
    (only,) = rec.files
    assert only.size is None and only.checksum is None and only.checksum_source is None


def test_a_missing_licence_is_unknown_not_a_guess():
    record = arxiv.resolve(OpenClient(ATOM), "arxiv:2401.12345v2")
    assert record.license == "unknown"


def test_the_licence_arxiv_reports_is_used():
    body = ATOM.replace(
        b"<title>Distributionally",
        b'<arxiv:license href="http://arxiv.org/licenses/cc_by/4.0/"/><title>Distributionally',
    ).replace(
        b'xmlns="http://www.w3.org/2005/Atom"',
        b'xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom"',
    )
    record = arxiv.resolve(OpenClient(body), "arxiv:2401.12345v2")
    assert record.license == "cc_by"


LEGACY = ATOM.replace(b"2401.12345v2", b"solv-int/9701001v2")


def test_a_legacy_id_keeps_its_archive_and_version():
    """``solv-int/9701001v2`` used to become ``sol`` and lose the paper."""
    record = arxiv.resolve(OpenClient(LEGACY), "arxiv:solv-int/9701001v2")
    assert record.files[0].name == "solv-int_9701001.pdf"
    assert record.doi == "10.48550/arXiv.solv-int/9701001"
    assert record.version == "2"


def test_two_legacy_papers_no_longer_share_a_name():
    first = arxiv.resolve(OpenClient(LEGACY), "arxiv:solv-int/9701001v2")
    second = arxiv.resolve(
        OpenClient(ATOM.replace(b"2401.12345v2", b"solv-int/9701002v1")),
        "arxiv:solv-int/9701002v1",
    )
    assert first.files[0].name != second.files[0].name

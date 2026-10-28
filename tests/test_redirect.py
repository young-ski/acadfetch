from __future__ import annotations

import urllib.request

from acadfetch import downloader
from acadfetch.client import Client, RedirectPolicy
from acadfetch.sources.base import File, Record

TOKEN = {"zenodo": {"Authorization": "Bearer secret-value"}}


def _client() -> Client:
    client = Client(rate=1000, retries=0, auth=TOKEN)
    client.source = "zenodo"
    return client


def _record(server, path="/f.bin", payload=b"payload"):
    server.cfg["files"][path] = payload
    return Record(
        source="zenodo",
        target="zenodo:1",
        files=[File(server.base + path, "f.bin", len(payload))],
    )


def test_a_redirect_to_another_origin_drops_the_token(server, make_server, tmp_path):
    """Zenodo hands downloads to storage hosts; the token is Zenodo's, not theirs."""
    other = make_server()
    rec = _record(server)
    server.cfg["redirects"]["/f.bin"] = other.base + "/f.bin"
    other.cfg["files"]["/f.bin"] = b"payload"

    res = downloader.fetch_file(_client(), rec.files[0], downloader.Options(out=tmp_path), set())

    assert res.state == "downloaded"
    assert server.cfg["auth_seen"] == ["Bearer secret-value"]
    assert other.cfg["auth_seen"] == [None], "the token left its origin"


def test_a_redirect_within_the_origin_keeps_the_token(server, tmp_path):
    rec = _record(server)
    server.cfg["files"]["/moved.bin"] = b"payload"
    server.cfg["redirects"]["/f.bin"] = server.base + "/moved.bin"

    res = downloader.fetch_file(_client(), rec.files[0], downloader.Options(out=tmp_path), set())

    assert res.state == "downloaded"
    assert server.cfg["auth_seen"] == ["Bearer secret-value", "Bearer secret-value"]


def test_a_presigned_url_gets_no_authorization_header(server, tmp_path):
    """S3-style presigned URLs reject a request that also carries a bearer token."""
    url = server.base + "/f.bin?X-Amz-Signature=abc&X-Amz-Credential=x"
    server.cfg["files"]["/f.bin"] = b"payload"
    f = File(url, "f.bin", 7)

    res = downloader.fetch_file(_client(), f, downloader.Options(out=tmp_path), set())

    assert res.state == "downloaded"
    assert server.cfg["auth_seen"] == [None]


def test_an_https_to_http_downgrade_is_recorded():
    """http still works, but it has to be visible that the hop is unprotected."""
    policy = RedirectPolicy()
    req = urllib.request.Request("https://example.org/a", headers={"Authorization": "Bearer x"})

    new = policy.redirect_request(req, None, 302, "Found", {}, "http://example.org/b")

    assert new is not None
    assert policy.downgrades == [("https://example.org/a", "http://example.org/b")]
    assert new.headers.get("Authorization") == "Bearer x", "same origin keeps the token"

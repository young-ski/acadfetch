from __future__ import annotations

import json

from acadfetch import auth, manifest
from acadfetch.client import Client
from acadfetch.sources.base import File, Record

TOKEN = "zenodo-super-secret-1234"


def test_load_empty():
    assert auth.load({}) == {}


def test_load_reads_known_variables():
    tokens = auth.load({"ZENODO_TOKEN": TOKEN, "FIGSHARE_TOKEN": "fig-0000-abcd"})
    assert set(tokens) == {"zenodo", "figshare"}
    assert tokens["zenodo"].env == "ZENODO_TOKEN"


def test_load_strips_whitespace_and_ignores_blank():
    assert auth.load({"ZENODO_TOKEN": "   "}) == {}
    assert auth.load({"ZENODO_TOKEN": f"  {TOKEN}\n"})["zenodo"].value == TOKEN


def test_hint_does_not_leak_the_body():
    token = auth.load({"ZENODO_TOKEN": TOKEN})["zenodo"]
    assert token.hint == "…1234"
    assert TOKEN not in token.hint
    assert TOKEN not in str(token)


def test_hint_on_short_token():
    assert auth.load({"ZENODO_TOKEN": "abc"})["zenodo"].hint == "…"


def test_headers_are_bearer():
    tokens = auth.load({"ZENODO_TOKEN": TOKEN})
    assert auth.headers(tokens, "zenodo") == {"Authorization": f"Bearer {TOKEN}"}


def test_headers_empty_for_other_or_unknown_sources():
    tokens = auth.load({"ZENODO_TOKEN": TOKEN})
    assert auth.headers(tokens, "arxiv") == {}
    assert auth.headers(tokens, None) == {}


def test_describe_mentions_variable_only():
    tokens = auth.load({"ZENODO_TOKEN": TOKEN})
    text = auth.describe(tokens, "zenodo")
    assert "ZENODO_TOKEN" in text and "1234" in text and TOKEN not in text


# -- client wiring --------------------------------------------------------


def _client(**kw) -> Client:
    tokens = auth.load({"ZENODO_TOKEN": TOKEN})
    return Client(
        rate=100,
        auth={source: token.headers for source, token in tokens.items()},
        **kw,
    )


def test_client_attaches_token_for_matching_source():
    client = _client()
    client.source = "zenodo"
    req = client._request("https://zenodo.org/api/records/1")
    assert req.get_header("Authorization") == f"Bearer {TOKEN}"


def test_client_omits_token_for_other_sources():
    client = _client()
    client.source = "arxiv"
    assert client._request("https://arxiv.org/abs/1").get_header("Authorization") is None
    client.source = None
    assert client._request("https://example.org/x").get_header("Authorization") is None


def test_explicit_header_beats_token():
    client = _client(headers={"Authorization": "Bearer manual-9999"})
    client.source = "zenodo"
    assert (
        client._request("https://zenodo.org/x").get_header("Authorization") == "Bearer manual-9999"
    )


def test_client_still_sets_user_agent_with_token():
    client = _client()
    client.source = "zenodo"
    assert "acadfetch" in client._request("https://zenodo.org/x").get_header("User-agent")


def test_token_never_reaches_the_manifest(tmp_path):
    tokens = auth.load({"ZENODO_TOKEN": TOKEN})
    record = Record(source="zenodo", target="zenodo:1", files=[File("https://zenodo.org/f", "f")])
    payload = manifest.build(record, [], tokens["zenodo"])
    text = json.dumps(payload)
    assert payload["auth"] == {"env": "ZENODO_TOKEN", "hint": "…1234"}
    assert TOKEN not in text


def test_manifest_auth_is_null_without_token():
    record = Record(source="zenodo", target="zenodo:1", files=[])
    assert manifest.build(record, [])["auth"] is None

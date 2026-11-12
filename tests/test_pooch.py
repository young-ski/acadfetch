"""The pooch downloader, driven through pooch's own machinery.

These run the real ``pooch.retrieve`` and ``Pooch.is_available`` against the local
test server, so the contract under test is pooch's, not a paraphrase of it. The
point is that acadfetch only supplies transport: pooch must still do the hashing
and the move, and must still be the one to fail on a bad hash.
"""

from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pooch
import pytest

from acadfetch import naming
from acadfetch.client import Client
from acadfetch.pooch import Downloader, downloader

PAYLOAD = b"pooch payload" * 500
MD5 = hashlib.md5(PAYLOAD).hexdigest()


def _serve(server, path="/a.bin", payload=PAYLOAD):
    server.cfg["files"][path] = payload
    return server.base + path


def test_pooch_gets_the_bytes_and_verifies_them(server, tmp_path):
    url = _serve(server)

    got = pooch.retrieve(url, known_hash=f"md5:{MD5}", downloader=downloader, path=str(tmp_path))

    assert Path(got).read_bytes() == PAYLOAD
    assert server.cfg["hits"]["/a.bin"] == 1
    # pooch names the cached file "<hash>-a.bin" and moves it into place; we must
    # not have left a .part or anything else behind.
    kept = list(tmp_path.iterdir())
    assert len(kept) == 1 and kept[0].name.endswith("-a.bin"), kept


def test_a_bad_hash_is_still_pooch_that_refuses_it(server, tmp_path):
    """The downloader must not be able to talk pooch out of a mismatch."""
    url = _serve(server)

    with pytest.raises(ValueError) as caught:
        pooch.retrieve(url, known_hash="md5:" + "0" * 32, downloader=downloader, path=str(tmp_path))

    assert "hash" in str(caught.value).lower()
    assert list(tmp_path.iterdir()) == [], "a rejected download must not be kept"


def test_a_truncated_response_is_resumed_within_the_attempt(server, tmp_path):
    """One cut connection must cost a chunk, not the whole file."""
    url = _serve(server)
    server.cfg["truncate_at"] = 100
    server.cfg["truncate_times"] = 1

    got = pooch.retrieve(
        url, known_hash=f"md5:{MD5}", downloader=Downloader(rate=100), path=str(tmp_path)
    )

    assert Path(got).read_bytes() == PAYLOAD
    assert server.cfg["ranges"][1] is not None, "the retry should have asked for a range"


def test_a_missing_file_is_reported_to_the_caller(server, tmp_path):
    url = server.base + "/gone.bin"

    with pytest.raises(Exception) as caught:
        pooch.retrieve(url, known_hash=None, downloader=downloader, path=str(tmp_path))

    assert "404" in str(caught.value)


def test_availability_answers_without_downloading(server, tmp_path):
    _serve(server)
    pool = pooch.Pooch(path=str(tmp_path), base_url=server.base + "/")
    pool.registry["a.bin"] = None

    assert pool.is_available("a.bin", downloader=downloader) is True
    # A HEAD is expected; a GET of the body is not.
    assert server.cfg["ranges"] == [], "availability must not fetch the body"


def test_a_missing_file_is_not_available(server, tmp_path):
    pool = pooch.Pooch(path=str(tmp_path), base_url=server.base + "/")
    pool.registry["gone.bin"] = None

    assert pool.is_available("gone.bin", downloader=downloader) is False


def test_a_header_survives_to_the_origin(server, tmp_path):
    """A restricted record is reached by handing the token to the downloader."""
    url = _serve(server)
    configured = Downloader(headers={"Authorization": "Bearer secret"})

    pooch.retrieve(url, known_hash=f"md5:{MD5}", downloader=configured, path=str(tmp_path))

    assert server.cfg["auth_seen"][0] == "Bearer secret"


def test_a_caller_supplied_handle_is_written_through(server, tmp_path):
    """pooch's docs allow a file-like object; pooch owns it, so we do not rename."""
    url = _serve(server)
    buffer = io.BytesIO()

    assert downloader(url, buffer) is None
    assert buffer.getvalue() == PAYLOAD


def test_no_output_file_is_a_clear_error(server):
    with pytest.raises(Exception) as caught:
        downloader(_serve(server))

    assert "no output file" in str(caught.value)


def test_a_name_the_sanitiser_changes_still_lands_where_the_caller_asked(server, tmp_path):
    """`naming` is allowed to rewrite a name; pooch's requested path wins."""
    url = _serve(server)
    target = tmp_path / "data .bin"
    assert target.name != naming.sanitize(target.name), "this test needs a rewritable name"

    downloader(url, target)

    assert target.read_bytes() == PAYLOAD
    assert not (tmp_path / naming.sanitize(target.name)).exists()


def test_a_head_that_fails_is_not_available(server):
    assert downloader.available(server.base + "/gone.bin") is False


def test_an_empty_head_is_not_read_as_available(server, monkeypatch):
    """A 403/405/501 loses its headers; that must not count as success."""
    monkeypatch.setattr(Client, "head", lambda self, url: {})

    assert downloader.available(server.base + "/gone.bin") is False


def test_an_empty_head_still_finds_a_file_that_is_there(server, monkeypatch):
    """The fallback exists so a 405 on HEAD does not make a real file look absent."""
    url = _serve(server)
    monkeypatch.setattr(Client, "head", lambda self, url: {})

    assert downloader.available(url) is True

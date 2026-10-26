from __future__ import annotations

import email.utils
import io
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from acadfetch import client as client_mod
from acadfetch.client import Client, is_block_page, parse_retry_after
from acadfetch.errors import HttpError

FIXTURES = Path(__file__).parent / "fixtures"
BLOCK = (FIXTURES / "figshare_403_block.html").read_bytes()
FORBIDDEN = (FIXTURES / "figshare_403_forbidden.json").read_bytes()
URL = "https://api.figshare.com/v2/articles/13054873"


def test_numeric_seconds():
    assert parse_retry_after("7") == 7.0


def test_clamped_to_max():
    assert parse_retry_after("999999") == 120.0


def test_http_date():
    when = datetime.now(timezone.utc) + timedelta(seconds=30)
    value = email.utils.format_datetime(when, usegmt=True)
    assert 25 <= parse_retry_after(value) <= 31


def test_past_date_is_zero():
    when = datetime.now(timezone.utc) - timedelta(seconds=30)
    assert parse_retry_after(email.utils.format_datetime(when, usegmt=True)) == 0.0


def test_junk():
    assert parse_retry_after("soon") is None
    assert parse_retry_after(None) is None


# -- block page vs. real permission error ---------------------------------


def test_html_body_is_a_block_page():
    assert is_block_page("<html><head><title>403 Forbidden</title>", "text/html")


def test_html_content_type_is_a_block_page_even_without_markup():
    assert is_block_page("Request blocked", "text/html; charset=UTF-8")


def test_empty_body_is_a_block_page():
    assert is_block_page("", "text/plain")
    assert is_block_page("   ", None)


def test_json_body_is_a_real_permission_error():
    assert not is_block_page('{"message": "Forbidden", "code": "Forbidden"}', "application/json")


def test_plain_text_body_is_a_real_permission_error():
    assert not is_block_page("Forbidden", None)


class Resp:
    def __init__(self, body: bytes = b"{}"):
        self.body = body

    def read(self, n=None):
        body, self.body = self.body, b""
        return body[:n] if n is not None else body

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(code: int, body: bytes, content_type: str, **extra) -> urllib.error.HTTPError:
    headers = {"Content-Type": content_type, **extra}
    return urllib.error.HTTPError(URL, code, "boom", headers, io.BytesIO(body))


@pytest.fixture
def fast(monkeypatch):
    """A client that never really sleeps, plus scripted responses."""
    monkeypatch.setattr(client_mod.time, "sleep", lambda _s: None)

    def install(script):
        calls = []

        def fake(_req):
            calls.append(len(calls))
            step = script.pop(0)
            if isinstance(step, Exception):
                raise step
            return Resp(step)

        monkeypatch.setattr(Client, "_open", staticmethod(fake))
        return calls

    return install


def test_block_page_is_retried_then_succeeds(fast):
    calls = fast([http_error(403, BLOCK, "text/html"), http_error(403, BLOCK, "text/html"), b"ok"])
    with Client(rate=1000, retries=4).open(URL) as resp:
        assert resp.read() == b"ok"
    assert len(calls) == 3


def test_block_page_retries_are_capped(fast):
    script = [http_error(403, BLOCK, "text/html") for _ in range(10)]
    calls = fast(script)
    with pytest.raises(HttpError) as info:
        Client(rate=1000, retries=9).open(URL)
    assert info.value.status == 403
    assert info.value.transient is True
    assert len(calls) == client_mod.consts.WAF_403_RETRIES + 1


def test_empty_body_403_is_retried(fast):
    calls = fast([http_error(403, b"", "text/plain"), b"ok"])
    with Client(rate=1000, retries=4).open(URL) as resp:
        assert resp.read() == b"ok"
    assert len(calls) == 2


def test_permission_403_fails_on_the_first_try(fast):
    calls = fast([http_error(403, FORBIDDEN, "application/json")])
    with pytest.raises(HttpError) as info:
        Client(rate=1000, retries=9).open(URL)
    assert info.value.status == 403
    assert info.value.transient is False
    assert "Forbidden" in str(info.value)
    assert len(calls) == 1


def test_head_tolerates_a_block_page_without_retrying(fast):
    """HEAD is only advisory (size, filename): do not spend retries on it."""
    calls = fast([http_error(403, BLOCK, "text/html") for _ in range(9)])
    assert Client(rate=1000, retries=4).head(URL) == {}
    assert len(calls) == 1


def test_block_retries_zero_makes_a_block_page_final(fast):
    calls = fast([http_error(403, BLOCK, "text/html"), b"never"])
    with pytest.raises(HttpError) as info:
        Client(rate=1000, retries=4, block_retries=0).open(URL)
    assert info.value.status == 403
    assert info.value.transient is True
    assert "gave up after 0 retries" in str(info.value)
    assert len(calls) == 1


def test_block_retries_can_be_raised(fast):
    script = [http_error(403, BLOCK, "text/html") for _ in range(4)] + [b"ok"]
    calls = fast(script)
    with Client(rate=1000, retries=9, block_retries=4).open(URL) as resp:
        assert resp.read() == b"ok"
    assert len(calls) == 5


def test_negative_block_retries_is_clamped():
    assert Client(rate=1000, block_retries=-3).block_retries == 0


def test_head_does_not_spend_retries_on_a_500(fast):
    calls = fast([http_error(500, b"", "text/plain") for _ in range(9)])
    with pytest.raises(HttpError):
        Client(rate=1000, retries=4).head(URL)
    assert len(calls) == 1


def test_retry_after_zero_still_leaves_a_gap(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(client_mod.time, "sleep", slept.append)
    script = [
        http_error(429, b"", "text/plain", **{"Retry-After": "0"}),
        http_error(429, b"", "text/plain", **{"Retry-After": "0"}),
        b"ok",
    ]

    def fake(_req):
        step = script.pop(0)
        if isinstance(step, Exception):
            raise step
        return Resp(step)

    monkeypatch.setattr(Client, "_open", staticmethod(fake))
    with Client(rate=1000, retries=4).open(URL) as resp:
        assert resp.read() == b"ok"
    assert len(slept) == 2
    assert min(slept) >= 0.05

from __future__ import annotations

import pytest

from acadfetch.urls import redact, redact_text

SIGNED = (
    "https://bucket.example/data.csv"
    "?X-Amz-Signature=SECRET123&X-Amz-Credential=AKIA&X-Amz-Expires=900&Expires=1"
)


def test_credential_parameters_go():
    out = redact(SIGNED)
    assert "SECRET123" not in out
    assert "AKIA" not in out
    assert "X-Amz-Expires=900" in out, "non-secret parameters stay"
    assert out.startswith("https://bucket.example/data.csv?")


def test_a_plain_url_is_untouched():
    assert redact("https://example.org/a.csv?page=2") == "https://example.org/a.csv?page=2"
    assert redact("https://example.org/a.csv") == "https://example.org/a.csv"


def test_a_bare_signature_and_token_are_covered():
    assert "s3cr3t" not in redact("https://x/y?signature=s3cr3t")
    assert "abc" not in redact("https://x/y?access_token=abc")


def test_text_redaction_only_touches_urls():
    sentence = f"HTTP 0 for {SIGNED}: connection refused"
    out = redact_text(sentence)
    assert "SECRET123" not in out
    assert "connection refused" in out
    assert "Expires=1: connection" in out, "the colon after the URL is not swallowed"


def test_text_redaction_leaves_short_urls_alone():
    assert redact_text("see https://x.io, then stop") == "see https://x.io, then stop"


@pytest.mark.parametrize("value", ["", "not a url", "https://x/y?X-Amz-Signature=a%20b"])
def test_odd_input_does_not_raise(value):
    assert isinstance(redact(value), str)
    assert isinstance(redact_text(value), str)

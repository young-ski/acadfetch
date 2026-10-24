from __future__ import annotations

import email.utils
from datetime import datetime, timedelta, timezone

from acadfetch.client import parse_retry_after


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

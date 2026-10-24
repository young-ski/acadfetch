from __future__ import annotations

import time

import pytest

from acadfetch.ratelimit import TokenBucket


def test_rejects_nonpositive_rate():
    with pytest.raises(ValueError):
        TokenBucket(0)


def test_burst_then_throttle():
    bucket = TokenBucket(rate=20, burst=2)
    start = time.monotonic()
    for _ in range(5):
        bucket.acquire()
    elapsed = time.monotonic() - start
    # 5 tokens, 2 free, 20/s -> ~0.15s
    assert 0.10 <= elapsed <= 0.60


def test_burst_never_exceeds_capacity():
    bucket = TokenBucket(rate=50, burst=3)
    start = time.monotonic()
    for _ in range(30):
        bucket.acquire()
    assert time.monotonic() - start >= 0.4

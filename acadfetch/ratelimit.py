"""Global token-bucket rate limiter.

Deliberately one bucket for the whole process, not per-thread: repositories
rate-limit by IP, and the point of the limiter is to stay under that limit.
"""

from __future__ import annotations

import threading
import time


class TokenBucket:
    def __init__(self, rate: float, burst: float | None = None) -> None:
        if rate <= 0:
            raise ValueError("rate must be > 0")
        self._rate = rate
        self._capacity = max(burst if burst is not None else rate, 1.0)
        self._tokens = self._capacity
        self._stamp = time.monotonic()
        self._lock = threading.Lock()

    def _refill(self) -> None:
        now = time.monotonic()
        self._tokens = min(self._capacity, self._tokens + (now - self._stamp) * self._rate)
        self._stamp = now

    def acquire(self) -> float:
        """Block until a token is available. Returns the seconds spent waiting."""
        while True:
            with self._lock:
                self._refill()
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return 0.0
                wait = (1.0 - self._tokens) / self._rate
            time.sleep(wait)

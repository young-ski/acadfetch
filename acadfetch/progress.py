"""Aggregate transfer progress.

One line for the whole run, not one per file: with ``-j 3`` three downloads are
in flight and three interleaved lines are unreadable. On a TTY the line is
redrawn in place; when stdout is a pipe (CI, ``tee``) it degrades to occasional
whole lines so the log stays greppable.
"""

from __future__ import annotations

import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import TextIO

from .units import duration, human

SMOOTHING_SECONDS = 1.0
TTY_INTERVAL = 0.15
PIPE_INTERVAL = 5.0
MAX_WIDTH = 120
NAME_WIDTH = 40


def _clip(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


class Progress:
    def __init__(
        self,
        stream: TextIO | None = None,
        enabled: bool = True,
        is_tty: bool | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.stream = stream if stream is not None else sys.stdout
        self.enabled = enabled
        self.is_tty = (self.stream.isatty() if is_tty is None else is_tty) and enabled
        self._clock = clock
        self._lock = threading.RLock()  # _render() re-enters under _maybe_emit
        self._active: dict[str, int] = {}
        self._done = 0
        self._total = 0
        self._samples: deque[tuple[float, int]] = deque()
        self._last_emit = 0.0
        self._printed = 0
        self._last_count = 0
        self._dirty = False

    # -- structure ---------------------------------------------------------
    def start(self, name: str, size: int | None) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._active[name] = size or 0
            self._total += size or 0
            self._sample()
        self._maybe_emit(force=True)

    def stop(self, name: str) -> None:
        if not self.enabled:
            return
        self._maybe_emit(force=True)  # one last frame that still counts the file
        with self._lock:
            self._active.pop(name, None)

    def advance(self, count: int) -> None:
        if not self.enabled or count <= 0:
            return
        with self._lock:
            self._done += count
            self._sample()
        self._maybe_emit()

    # -- rendering ---------------------------------------------------------
    def _sample(self) -> None:
        now = self._clock()
        self._samples.append((now, self._done))
        while len(self._samples) > 2 and now - self._samples[0][0] > SMOOTHING_SECONDS * 4:
            self._samples.popleft()
        self._dirty = True

    def _speed(self) -> float:
        if len(self._samples) < 2:
            return 0.0
        (t0, b0), (t1, b1) = self._samples[0], self._samples[-1]
        span = t1 - t0
        if span < 0.2:
            return 0.0
        return max(0.0, (b1 - b0) / span)

    def _eta(self, speed: float) -> float | None:
        if not self._total or speed <= 0:
            return None
        return max(0.0, (self._total - self._done) / speed)

    def _render(self) -> str:
        speed = self._speed()
        with self._lock:
            active = list(self._active)
        # After the last file stops there is nothing active; keep showing how
        # many files the run covered instead of dropping to "0 file(s)".
        count = len(active) or self._last_count
        self._last_count = count
        parts = [f"{count} file(s)", f"{human(self._done)}"]
        if len(active) == 1:
            parts[0] = _clip(active[0], NAME_WIDTH)
        if self._total:
            parts[1] = f"{human(self._done)}/{human(self._total)}"
        if speed > 0:
            parts.append(f"{human(speed)}/s")
            eta = self._eta(speed)
            if eta is not None:
                parts.append(f"ETA {duration(eta)}")
        line = "  ".join(parts)
        return line[:MAX_WIDTH]

    def _maybe_emit(self, force: bool = False) -> None:
        if not self.enabled:
            return
        now = self._clock()
        interval = TTY_INTERVAL if self.is_tty else PIPE_INTERVAL
        with self._lock:
            if not self._dirty:
                return
            if not force and now - self._last_emit < interval:
                return
            line = self._render()
            self._last_emit = now
            self._dirty = False
        self._write(line, newline=not self.is_tty)

    def _write(self, line: str, newline: bool) -> None:
        try:
            if self.is_tty:
                pad = max(0, self._printed - len(line))
                self.stream.write("\r" + line + " " * pad)
                self._printed = len(line)
            else:
                self.stream.write(line + ("\n" if newline else ""))
            self.stream.flush()
        except (OSError, ValueError):  # stream closed under us
            self.enabled = False

    def finish(self) -> None:
        if not self.enabled:
            return
        line = self._render()
        self._write(line, newline=True)
        if self.is_tty:
            self.stream.write("\n")
            self.stream.flush()
        self.enabled = False

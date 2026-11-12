"""Aggregate transfer progress.

One line for the whole run, not one per file: with ``-j 3`` three downloads are
in flight and three interleaved lines are unreadable. On a TTY the line is
redrawn in place; when stdout is a pipe (CI, ``tee``) it degrades to occasional
whole lines so the log stays greppable.
"""

from __future__ import annotations

import math
import shutil
import sys
import threading
import time
import unicodedata
from collections.abc import Callable
from typing import TextIO

from .units import duration, human

#: Time constant of the speed estimate. Long enough that one slow chunk does not
#: move the ETA by minutes, short enough to follow a link that really did change.
SMOOTHING_SECONDS = 5.0
TTY_INTERVAL = 0.15
PIPE_INTERVAL = 5.0
MAX_WIDTH = 120
#: Below this a two-part line has no room to say anything useful.
MIN_WIDTH = 40
NAME_WIDTH = 40


def _char_width(char: str) -> int:
    return 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1


def _width(text: str) -> int:
    """Terminal columns a string occupies; CJK glyphs are two, not one."""
    return sum(_char_width(char) for char in text)


def _clip(text: str, limit: int) -> str:
    """Cut to ``limit`` columns, counting wide characters, and mark the cut."""
    if _width(text) <= limit:
        return text
    kept = []
    used = 0
    for char in text:
        step = _char_width(char)
        if used + step > limit - 1:
            break
        kept.append(char)
        used += step
    return "".join(kept) + "…"


class Progress:
    def __init__(
        self,
        stream: TextIO | None = None,
        enabled: bool = True,
        is_tty: bool | None = None,
        clock: Callable[[], float] = time.monotonic,
        width: int | None = None,
    ) -> None:
        self.stream = stream if stream is not None else sys.stdout
        self.enabled = enabled
        self.is_tty = (self.stream.isatty() if is_tty is None else is_tty) and enabled
        self._clock = clock
        self._lock = threading.RLock()  # _render() re-enters under _maybe_emit
        # A wrapped line is worse than a shorter one: the carriage return only
        # clears the last visual row, so the redraw smears. Ask the terminal.
        if width is not None:
            self.width = max(MIN_WIDTH, min(width, MAX_WIDTH))
        elif self.is_tty:
            columns = shutil.get_terminal_size((MAX_WIDTH, 24)).columns
            self.width = max(MIN_WIDTH, min(columns, MAX_WIDTH))
        else:
            # A pipe has no viewport to wrap in; 120 is the readability cap.
            self.width = MAX_WIDTH
        self._active: dict[str, int] = {}
        self._done = 0
        self._total = 0
        self._rate = 0.0
        self._last_at: float | None = None
        self._last_done = 0
        self._last_emit = 0.0
        self._printed = 0
        self._last_count = 0
        self._dirty = False
        self._wrote = False

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
        """Fold the bytes since the last sample into an exponential average.

        The old estimate divided the window's byte delta by its time span, so a
        single slow chunk moved the rate by a factor of two and the ETA by
        minutes -- observed jumping from 4:55 to 0:28 inside one run. An average
        with a fixed time constant settles instead.
        """
        now = self._clock()
        if self._last_at is not None:
            dt = now - self._last_at
            if dt > 0:
                instant = max(0.0, (self._done - self._last_done) / dt)
                alpha = 1.0 - math.exp(-dt / SMOOTHING_SECONDS)
                self._rate += alpha * (instant - self._rate)
        self._last_at, self._last_done = now, self._done
        self._dirty = True

    def _speed(self) -> float:
        return self._rate

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
        return _clip("  ".join(parts), self.width)

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
                pad = max(0, self._printed - _width(line))
                self.stream.write("\r" + line + " " * pad)
                self._printed = _width(line)
            else:
                self.stream.write(line + ("\n" if newline else ""))
            self.stream.flush()
            self._wrote = True
        except (OSError, ValueError):  # stream closed under us
            self.enabled = False

    def finish(self) -> None:
        if not self.enabled:
            return
        # A run where every file was already on disk never started one, so a
        # frame here would only say "0 file(s) 0 B" right before the tally.
        if self._wrote:
            line = self._render()
            self._write(line, newline=True)
            if self.is_tty:
                self.stream.write("\n")
                self.stream.flush()
        self.enabled = False

from __future__ import annotations

import io

import pytest

from acadfetch.progress import MAX_WIDTH, Progress


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def tick(self, seconds: float) -> None:
        self.now += seconds


def make(tmp_path=None, tty=False, enabled=True):
    stream = io.StringIO()
    clock = Clock()
    prog = Progress(stream=stream, enabled=enabled, is_tty=tty, clock=clock)
    return prog, stream, clock


def test_disabled_writes_nothing():
    prog, stream, _ = make(enabled=False)
    prog.start("a", 10)
    prog.advance(5)
    prog.finish()
    assert stream.getvalue() == ""


def test_tty_redraws_one_line():
    prog, stream, clock = make(tty=True)
    prog.start("a", 1_000_000)
    prog.advance(500_000)
    clock.tick(1)
    prog.advance(100_000)
    text = stream.getvalue()
    assert text.startswith("\r")
    assert text.count("\n") == 0


def test_tty_pads_to_clear_previous_line():
    prog, stream, _ = make(tty=True)
    prog._write("a long first line here", newline=False)
    prog._write("short", newline=False)
    assert stream.getvalue() == "\ra long first line here\r" + "short" + " " * 17


def test_finish_closes_the_line_on_a_tty():
    prog, stream, _ = make(tty=True)
    prog.start("a", 100)
    prog.advance(100)
    prog.finish()
    assert stream.getvalue().endswith("\n\n") or stream.getvalue().count("\n") >= 1


def test_pipe_mode_writes_whole_lines():
    prog, stream, clock = make(tty=False)
    prog.start("a", 1_000_000)
    prog.advance(1_000_000)
    clock.tick(10)
    prog.advance(0)
    assert "\r" not in stream.getvalue()
    assert "\n" in stream.getvalue()


def test_aggregates_multiple_files():
    prog, stream, clock = make(tty=True)
    prog.start("a", 1000)
    prog.start("b", 1000)
    prog.start("c", None)
    prog.advance(500)
    clock.tick(1)
    prog.advance(500)
    assert "3 file(s)" in stream.getvalue()


def test_speed_and_eta_render():
    prog, _, clock = make()
    prog.start("a", 4_000_000)
    prog.advance(1_000_000)
    clock.tick(2)
    prog.advance(1_000_000)
    line = prog._render()
    assert "KiB/s" in line or "MiB/s" in line
    assert "ETA" in line


def test_no_eta_without_a_known_total():
    prog, _, clock = make()
    prog.start("a", None)
    prog.advance(100_000)
    clock.tick(2)
    prog.advance(100_000)
    line = prog._render()
    assert "ETA" not in line
    assert "195.3 KiB" in line


def test_no_speed_without_elapsed_time():
    prog, _, _ = make()
    prog.start("a", 1000)
    prog.advance(10)
    assert "/s" not in prog._render()


def test_line_is_width_capped():
    prog, _, _ = make()
    for i in range(40):
        prog.start(f"file-with-a-long-ish-name-{i}.tar.gz", 10**9)
    prog.advance(10**6)
    assert len(prog._render()) <= MAX_WIDTH


def test_throttle_suppresses_rapid_emits():
    prog, stream, _ = make(tty=False)
    prog.start("a", 10_000_000)
    before = stream.getvalue().count("\n")
    for _ in range(20):
        prog.advance(1024)
    assert stream.getvalue().count("\n") == before


def test_finish_is_idempotent():
    prog, stream, _ = make(tty=True)
    prog.start("a", 10)
    prog.advance(10)
    prog.finish()
    count = stream.getvalue().count("\n")
    prog.finish()
    assert stream.getvalue().count("\n") == count


def test_finish_stays_quiet_when_nothing_transferred():
    """Every file skipped: no frame, the per-file rows and the tally speak."""
    prog, stream, _ = make(tty=False)
    prog.finish()
    assert stream.getvalue() == ""
    assert prog.enabled is False


def test_finish_stays_quiet_on_a_tty_when_nothing_transferred():
    prog, stream, _ = make(tty=True)
    prog.finish()
    assert stream.getvalue() == ""


def test_one_transferred_file_still_gets_a_finish_frame():
    prog, stream, _ = make(tty=False)
    prog.start("a", 10)
    prog.advance(10)
    prog.finish()
    assert stream.getvalue().strip().split("\n")[-1].endswith("10 B")


def test_closed_stream_disables_instead_of_raising():
    prog = Progress(stream=io.StringIO(), enabled=True, is_tty=False)
    prog.stream.close()
    prog.start("a", 1)
    prog.advance(1)
    assert prog.enabled is False


def test_zero_and_negative_advance_are_ignored():
    prog, _, _ = make()
    prog.start("a", 100)
    prog.advance(0)
    prog.advance(-5)
    assert prog._done == 0


def test_stop_removes_file_from_the_active_count():
    prog, _, _ = make()
    prog.start("a", 100)
    prog.start("b", 100)
    prog.stop("a")
    assert len(prog._active) == 1


@pytest.mark.parametrize("size", [None, 0])
def test_totals_without_sizes(size):
    prog, _, _ = make()
    prog.start("a", size)
    assert prog._total == 0


def test_single_file_shows_its_name():
    prog, _, _ = make()
    prog.start("dataset.tar.gz", 100)
    assert prog._render().startswith("dataset.tar.gz  ")


def test_many_files_show_a_count_instead():
    prog, _, _ = make()
    prog.start("a", 100)
    prog.start("b", 100)
    assert prog._render().startswith("2 file(s)")


def test_long_names_are_clipped():
    prog, _, _ = make()
    prog.start("x" * 200, 100)
    assert "…" in prog._render()


def test_stop_emits_a_final_frame_that_still_counts_the_file():
    prog, stream, _ = make(tty=False)
    prog.start("a", 1000)
    prog.start("b", 1000)
    prog.advance(1000)
    prog.stop("a")
    frames = stream.getvalue().strip().split("\n")
    assert frames[-1].startswith("2 file(s)")
    assert frames[-1] != "0 file(s)"  # never a frame after the removal

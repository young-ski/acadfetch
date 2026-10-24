from __future__ import annotations

import pytest

from acadfetch import naming


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("data.csv", "data.csv"),
        ("../../etc/passwd", "passwd"),
        ("C:\\tmp\\file.txt", "file.txt"),
        ("a/b/c/figure.png", "figure.png"),
        ("weird:name?.csv", "weird_name_.csv"),
        ("tab\there.bin", "tab here.bin"),
    ],
)
def test_sanitize_strips_paths(raw, expected):
    assert naming.sanitize(raw) == expected


def test_sanitize_handles_nul_and_control():
    assert naming.sanitize("a\x00b\x1fc.txt") == "a_b_c.txt"


def test_sanitize_percent_decodes():
    assert naming.sanitize("na%C3%AFve.pdf") == "naïve.pdf"


def test_sanitize_reserved_windows_names():
    assert naming.sanitize("CON") == "_CON"
    assert naming.sanitize("nul.txt") == "_nul.txt"


def test_sanitize_empty_and_dots():
    assert naming.sanitize("") == "file"
    assert naming.sanitize("...") == "file"
    assert naming.sanitize("/") == "file"


def test_sanitize_truncates_but_keeps_extension():
    out = naming.sanitize("x" * 400 + ".tar.gz")
    assert len(out) <= 180
    assert out.endswith(".tar.gz")


def test_unique_is_case_insensitive():
    taken = set()
    assert naming.unique("data.csv", taken) == "data.csv"
    assert naming.unique("DATA.csv", taken) == "DATA (2).csv"
    assert naming.unique("Data.CSV", taken) == "Data (3).CSV"


def test_unique_keeps_no_extension_names():
    taken = {"build"}
    assert naming.unique("build", taken) == "build (2)"

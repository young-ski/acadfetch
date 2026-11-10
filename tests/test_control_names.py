from __future__ import annotations

import json

from acadfetch import cli
from acadfetch.sources.base import File, Record


def _run(capsys, *argv):
    code = cli.main([str(a) for a in argv])
    return code, *capsys.readouterr()


def _serve(server, names: dict[str, bytes]):
    for name, body in names.items():
        server.cfg["files"]["/" + name] = body


def _record(server, names: dict[str, bytes]):
    files = [File(server.base + "/" + n, n, len(b)) for n, b in names.items()]
    return Record(source="direct", target=server.base + "/record", files=files)


def test_a_repository_manifest_json_does_not_eat_ours(server, monkeypatch, capsys, tmp_path):
    names = {"manifest.json": b'{"theirs": true}', "a.bin": b"AAAA"}
    _serve(server, names)
    monkeypatch.setattr(cli.registry, "resolve", lambda _c, _t: _record(server, names))

    code, _, _ = _run(capsys, "fetch", "x", "-q", "-o", tmp_path)

    assert code == cli.EXIT_OK
    ours = json.loads((tmp_path / "manifest.json").read_text("utf-8"))
    assert ours["schema"] == 3
    assert sorted(row["name"] for row in ours["files"]) == ["a.bin", "manifest (2).json"]
    downloader_wrote = (tmp_path / "manifest (2).json").read_bytes()
    assert downloader_wrote == b'{"theirs": true}'


def test_a_repository_x_part_does_not_eat_the_sidecar(server, monkeypatch, capsys, tmp_path):
    names = {"x": b"P" * 80, "x.part": b"S" * 100}
    _serve(server, names)
    monkeypatch.setattr(cli.registry, "resolve", lambda _c, _t: _record(server, names))

    code, _, _ = _run(capsys, "fetch", "x", "-q", "-o", tmp_path)

    assert code == cli.EXIT_OK
    manifest = json.loads((tmp_path / "manifest.json").read_text("utf-8"))
    by_name = {row["name"]: row for row in manifest["files"]}
    assert (tmp_path / "x").read_bytes() == b"P" * 80
    assert (tmp_path / "x (2).part").read_bytes() == b"S" * 100
    assert by_name["x"]["bytes"] == 80
    assert by_name["x (2).part"]["bytes"] == 100


def test_the_plain_names_are_unchanged_without_a_collision(server, monkeypatch, capsys, tmp_path):
    names = {"a.bin": b"AAAA", "b.bin": b"BBBB"}
    _serve(server, names)
    monkeypatch.setattr(cli.registry, "resolve", lambda _c, _t: _record(server, names))

    _run(capsys, "fetch", "x", "-q", "-o", tmp_path)

    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "a.bin",
        "b.bin",
        "manifest.json",
    ]


def test_a_rerun_still_reuses_the_reserved_names(server, monkeypatch, capsys, tmp_path):
    """Reservation must not push every rerun onto a new name."""
    names = {"a.bin": b"AAAA"}
    _serve(server, names)
    monkeypatch.setattr(cli.registry, "resolve", lambda _c, _t: _record(server, names))

    _run(capsys, "fetch", "x", "-q", "-o", tmp_path)
    code, _, _ = _run(capsys, "fetch", "x", "-q", "-o", tmp_path)

    assert code == cli.EXIT_OK
    assert server.cfg["hits"]["/a.bin"] == 1
    assert (tmp_path / "a.bin").exists()

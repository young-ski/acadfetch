from __future__ import annotations

import argparse

import pytest

from acadfetch import cli, consts


@pytest.mark.parametrize(
    "text,expected",
    [("10", 10), ("500B", 500), ("1KB", 1024), ("1.5MiB", 1572864), ("2G", 2 << 30)],
)
def test_parse_size(text, expected):
    assert cli.parse_size(text) == expected


@pytest.mark.parametrize("text", ["", "abc", "10X", "-5"])
def test_parse_size_rejects(text):
    with pytest.raises(argparse.ArgumentTypeError):
        cli.parse_size(text)


def test_version_exits_zero(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    assert "acadfetch" in capsys.readouterr().out


def test_sources_lists_adapters(capsys):
    assert cli.main(["sources"]) == 0
    out = capsys.readouterr().out
    assert "zenodo" in out and "doi" in out


def test_bad_target_exit_1(capsys):
    assert cli.main(["manifest", "definitely not a target"]) == 1
    assert "cannot tell what" in capsys.readouterr().err


def test_headers_parse():
    assert cli._headers(["A: 1", "b:x:y"]) == {"A": "1", "b": "x:y"}
    assert cli._headers(None) == {}


def test_header_without_colon_exits_1(capsys):
    assert cli.main(["manifest", "zenodo:1", "--header", "oops"]) == 1
    assert "--header needs" in capsys.readouterr().err


def test_client_carries_headers():
    args = cli.build_parser().parse_args(["fetch", "zenodo:1", "--header", "X: y"])
    assert cli._client(args).headers == {"X": "y"}


def test_retry_403_flag_reaches_the_client():
    args = cli.build_parser().parse_args(["fetch", "zenodo:1", "--retry-403", "0"])
    assert cli._client(args).block_retries == 0


def test_retry_403_defaults_to_the_constant():
    args = cli.build_parser().parse_args(["fetch", "zenodo:1"])
    assert cli._client(args).block_retries == consts.WAF_403_RETRIES


@pytest.mark.parametrize("bad", ["-1", "x", "1.5"])
def test_retry_403_rejects(bad):
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["fetch", "zenodo:1", "--retry-403", bad])


def _stub_resolve(monkeypatch, source="zenodo"):
    from acadfetch.sources import registry
    from acadfetch.sources.base import File, Record

    def fake(client, target, allow_doi=True):
        client.source = source
        return Record(source=source, target=target, files=[File("https://x/f", "f")], doi="10.1/x")

    monkeypatch.setattr(registry, "resolve", fake)


def test_env_token_is_reported_redacted(monkeypatch, capsys, tmp_path):
    _stub_resolve(monkeypatch)
    monkeypatch.setenv("ZENODO_TOKEN", "tok-abcd-9999")
    code = cli.main(["manifest", "zenodo:1", "-o", str(tmp_path / "m.json")])
    out = capsys.readouterr().out
    assert code == 0
    assert "token from $ZENODO_TOKEN (…9999)" in out
    assert "tok-abcd-9999" not in out
    assert '"hint": "…9999"' in (tmp_path / "m.json").read_text("utf-8")


def test_no_token_skips_env_token(monkeypatch, capsys, tmp_path):
    _stub_resolve(monkeypatch)
    monkeypatch.setenv("ZENODO_TOKEN", "tok-abcd-9999")
    assert cli.main(["manifest", "zenodo:1", "-o", str(tmp_path / "m.json"), "--no-token"]) == 0
    assert "token from" not in capsys.readouterr().out


def test_token_only_loaded_for_its_own_source(monkeypatch):
    _stub_resolve(monkeypatch, source="arxiv")
    monkeypatch.setenv("ZENODO_TOKEN", "tok-abcd-9999")
    args = cli.build_parser().parse_args(["manifest", "arxiv:2401.1"])
    client = cli._client(args)
    client.source = "arxiv"
    assert client._request("https://arxiv.org/x").get_header("Authorization") is None
    client.source = "zenodo"
    assert client._request("https://zenodo.org/x").get_header("Authorization") is not None


def test_fully_skipped_run_prints_no_empty_progress_frame(monkeypatch, capsys, tmp_path):
    """The re-run case: nothing moves, so "0 file(s) 0 B" would contradict the tally."""
    _stub_resolve(monkeypatch)
    out = tmp_path / "data"
    out.mkdir()
    (out / "f").write_bytes(b"payload")
    assert cli.main(["fetch", "zenodo:1", "-o", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "skip f" in printed
    assert "0 file(s)" not in printed
    assert "1/1 file(s)" in printed


# -- verify: it must work on its own example, and never traceback ----------


def _manifest(tmp_path, files, schema=1):
    import json

    path = tmp_path / "data" / "manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema": schema, "files": files}), "utf-8")
    return path


def _good_entry(tmp_path):
    import hashlib

    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "a.bin").write_bytes(b"payload")
    return {"name": "a.bin", "sha256": hashlib.sha256(b"payload").hexdigest()}


def test_verify_resolves_names_against_the_manifest_dir(tmp_path, capsys):
    """The README shows `verify ./data/manifest.json` and expects it to work."""
    import os

    good = _good_entry(tmp_path)
    _manifest(tmp_path, [good])
    here = os.getcwd()
    os.chdir(tmp_path)
    try:
        assert cli.main(["verify", "./data/manifest.json"]) == 0
    finally:
        os.chdir(here)
    assert "ok a.bin" in capsys.readouterr().out


def test_verify_missing_manifest_is_one_line(tmp_path, capsys):
    assert cli.main(["verify", str(tmp_path / "nope.json")]) == 1
    err = capsys.readouterr().err
    assert "no such manifest" in err
    assert "Traceback" not in err


def test_verify_directory_is_one_line(tmp_path, capsys):
    assert cli.main(["verify", str(tmp_path)]) == 1
    err = capsys.readouterr().err
    assert "is a directory" in err
    assert "Traceback" not in err


def test_verify_bad_json_is_one_line(tmp_path, capsys):
    path = tmp_path / "m.json"
    path.write_text("{not json", "utf-8")
    assert cli.main(["verify", str(path)]) == 1
    err = capsys.readouterr().err
    assert "not valid JSON" in err
    assert "Traceback" not in err


def test_verify_non_object_json_is_one_line(tmp_path, capsys):
    path = tmp_path / "m.json"
    path.write_text("[1, 2]", "utf-8")
    assert cli.main(["verify", str(path)]) == 1
    assert "not a manifest object" in capsys.readouterr().err


def test_verify_future_schema_is_one_line(tmp_path, capsys):
    path = _manifest(tmp_path, [], schema=99)
    assert cli.main(["verify", str(path)]) == 1
    assert "unsupported manifest schema" in capsys.readouterr().err


def test_verify_entry_without_a_name_is_malformed(tmp_path, capsys):
    path = _manifest(tmp_path, [{"sha256": "0" * 64}])
    assert cli.main(["verify", str(path)]) == 1
    assert "malformed" in capsys.readouterr().out


def test_verify_empty_manifest_says_so(tmp_path, capsys):
    path = _manifest(tmp_path, [])
    assert cli.main(["verify", str(path)]) == 1
    assert "lists no files" in capsys.readouterr().err


def test_verify_root_still_overrides(tmp_path, capsys):
    good = _good_entry(tmp_path)
    path = _manifest(tmp_path, [good])
    assert cli.main(["verify", str(path), "--root", str(tmp_path / "data")]) == 0


def test_verify_mismatch_exits_1(tmp_path, capsys):
    good = _good_entry(tmp_path)
    path = _manifest(tmp_path, [good])
    (tmp_path / "data" / "a.bin").write_bytes(b"tampered")
    assert cli.main(["verify", str(path)]) == 1
    assert "MISMATCH" in capsys.readouterr().out


def test_retries_flag_reaches_the_transfer_layer(monkeypatch, capsys):
    """`--retries` must not stop at the Client; the transfer loop owns the budget."""
    _stub_resolve(monkeypatch)
    seen = {}

    def fake_fetch(record, opts, client, only=None):
        seen["retries"] = opts.retries
        return []

    monkeypatch.setattr(cli, "fetch_record", fake_fetch)
    assert cli.main(["fetch", "zenodo:1", "-o", ".tmp/x", "--retries", "0"]) == 0
    assert seen["retries"] == 0


# -- verify: names that leave --root -------------------------------------


def _entry_outside(tmp_path, relative="../outside.txt"):
    import hashlib

    outside = (tmp_path / "data").parent / "outside.txt"
    outside.write_bytes(b"outside")
    return {"name": relative, "sha256": hashlib.sha256(b"outside").hexdigest()}


def test_verify_flags_a_name_leaving_root(tmp_path, capsys):
    entry = _entry_outside(tmp_path)
    path = _manifest(tmp_path, [entry])
    assert cli.main(["verify", str(path)]) == 1
    out = capsys.readouterr()
    assert "escaped" in out.out
    assert "1/1 ok" not in out.out
    assert "--allow-escape" in out.err


def test_verify_follows_escapes_when_asked(tmp_path, capsys):
    entry = _entry_outside(tmp_path)
    path = _manifest(tmp_path, [entry])
    assert cli.main(["verify", str(path), "--allow-escape"]) == 0
    assert "ok" in capsys.readouterr().out


def test_verify_allows_a_dotdot_that_stays_inside(tmp_path, capsys):
    import hashlib

    (tmp_path / "data" / "sub").mkdir(parents=True)
    (tmp_path / "data" / "a.bin").write_bytes(b"payload")
    entry = {"name": "sub/../a.bin", "sha256": hashlib.sha256(b"payload").hexdigest()}
    path = _manifest(tmp_path, [entry])
    assert cli.main(["verify", str(path)]) == 0
    assert "ok" in capsys.readouterr().out


def test_verify_flags_an_absolute_name(tmp_path, capsys):
    import hashlib

    absolute = tmp_path / "abs.bin"
    absolute.write_bytes(b"abs")
    entry = {"name": str(absolute), "sha256": hashlib.sha256(b"abs").hexdigest()}
    path = _manifest(tmp_path, [entry])
    assert cli.main(["verify", str(path)]) == 1
    assert "escaped" in capsys.readouterr().out


def test_verify_flags_a_symlink_pointing_out(tmp_path, capsys):
    import hashlib
    import os

    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"linked")
    link = tmp_path / "data" / "link.bin"
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        import pytest

        pytest.skip("symlinks unavailable here")
    entry = {"name": "link.bin", "sha256": hashlib.sha256(b"linked").hexdigest()}
    path = _manifest(tmp_path, [entry])
    assert cli.main(["verify", str(path)]) == 1
    assert "escaped" in capsys.readouterr().out


def test_a_locked_manifest_is_one_line_not_a_crash(monkeypatch, capsys, tmp_path):
    """Windows refuses os.replace while another handle holds the target."""
    import acadfetch.manifest as mf

    _stub_resolve(monkeypatch)
    out = tmp_path / "data"
    out.mkdir()
    (out / "f").write_bytes(b"payload")

    def locked(path, data):
        raise PermissionError(13, "in use", str(path))

    monkeypatch.setattr(mf, "write", locked)
    code = cli.main(["fetch", "zenodo:1", "-o", str(out)])
    err = capsys.readouterr().err
    assert code == 2  # the bytes are fine, the manifest is not
    assert "could not write the manifest" in err
    assert "Traceback" not in err


def test_interrupt_exits_130_and_still_writes_the_manifest(monkeypatch, capsys, tmp_path):
    """A partial run must leave something to reason about, not just a bare 130."""
    from acadfetch.sources.base import Result

    _stub_resolve(monkeypatch)

    def fake_fetch(record, opts, client, only=None):
        opts.interrupted = True
        return [Result(record.files[0], "failed", error="interrupted")]

    monkeypatch.setattr(cli, "fetch_record", fake_fetch)
    out = tmp_path / "d"
    assert cli.main(["fetch", "zenodo:1", "-o", str(out)]) == 130
    printed = capsys.readouterr()
    assert "interrupted - partial files kept" in printed.err
    assert "interrupted" in printed.out
    assert (out / "manifest.json").exists()

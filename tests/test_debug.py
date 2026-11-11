from __future__ import annotations

import pytest

from acadfetch import cli, debug
from acadfetch.client import Client
from acadfetch.errors import HttpError


@pytest.fixture(autouse=True)
def _reset():
    debug.configure(False)
    yield
    debug.configure(False)


def test_the_environment_can_turn_it_on():
    debug.configure(False, {"ACADFETCH_DEBUG": "1"})
    assert debug.enabled()


@pytest.mark.parametrize("value", ["0", "false", "no", "off", ""])
def test_the_environment_can_turn_it_off(value):
    debug.configure(False, {"ACADFETCH_DEBUG": value})
    assert not debug.enabled()


def test_the_flag_wins_over_the_environment():
    debug.configure(True, {"ACADFETCH_DEBUG": "0"})
    assert debug.enabled()


def test_it_is_silent_when_off(capsys):
    debug.log("nothing")
    debug.log_exception(RuntimeError("boom"))
    assert capsys.readouterr().err == ""


def _talking_client():
    client = Client(rate=1000, retries=0, auth={"direct": {"Authorization": "Bearer SECRET"}})
    client.source = "direct"
    return client


def test_headers_are_masked(server, capsys):
    server.cfg["files"]["/a.bin"] = b"x"
    debug.configure(True)

    _talking_client().head(server.base + "/a.bin")

    err = capsys.readouterr().err
    assert "HEAD" in err
    assert "Authorization: …" in err
    assert "SECRET" not in err


def test_a_credential_in_the_url_is_redacted(server, capsys):
    server.cfg["files"]["/a.bin"] = b"x"
    debug.configure(True)

    _talking_client().head(server.base + "/a.bin?X-Amz-Signature=SIG")

    err = capsys.readouterr().err
    assert "X-Amz-Signature" not in err
    assert "SIG" not in err


def test_a_retry_is_traced(server, capsys):
    server.cfg["files"]["/a.bin"] = b"x"
    server.cfg["always_500"].add("/a.bin")
    debug.configure(True)

    with pytest.raises(HttpError):
        Client(rate=1000, retries=1).open(server.base + "/a.bin")

    err = capsys.readouterr().err
    assert "GET" in err and "-> 500" in err and "retry in" in err


def test_a_failure_prints_a_traceback_only_with_the_flag(capsys):
    assert cli.main(["fetch", "nonsense://x"]) == 1
    assert "Traceback" not in capsys.readouterr().err

    assert cli.main(["fetch", "nonsense://x", "--verbose"]) == 1
    assert "Traceback (most recent call last)" in capsys.readouterr().err


def test_debug_does_not_change_the_json_document(server, monkeypatch, capsys, tmp_path):
    import json

    server.cfg["files"]["/a.bin"] = b"payload"
    monkeypatch.setattr(
        cli.registry,
        "resolve",
        lambda _c, _t: __import__("acadfetch.sources.base", fromlist=["Record"]).Record(
            source="direct",
            target=server.base + "/a.bin",
            files=[
                __import__("acadfetch.sources.base", fromlist=["File"]).File(
                    server.base + "/a.bin", "a.bin", 7
                )
            ],
        ),
    )

    code = cli.main(["fetch", "t", "--json", "--verbose", "-o", str(tmp_path)])
    out, _ = capsys.readouterr()

    assert code == cli.EXIT_OK
    assert json.loads(out)["summary"]["ok"] is True

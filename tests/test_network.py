from __future__ import annotations

import urllib.request
from pathlib import Path

import pytest

from acadfetch import cli


def _args(*extra):
    return cli.build_parser().parse_args(["fetch", "zenodo:1", *extra])


def _proxies(client):
    """The explicit proxy mapping, or {} when urllib would keep no handler.

    A ProxyHandler with an empty mapping registers no method, so build_opener
    drops it; that is exactly what --no-proxy means.
    """
    found = [h for h in client.opener.handlers if isinstance(h, urllib.request.ProxyHandler)]
    return found[0].proxies if found else {}


@pytest.mark.parametrize("bad", ["0", "-1", "abc"])
def test_a_bad_timeout_is_a_usage_error(bad):
    with pytest.raises(SystemExit) as caught:
        cli.main(["fetch", "zenodo:1", "--timeout", bad])
    assert caught.value.code == cli.EXIT_USAGE


@pytest.mark.parametrize("bad", ["ftp://x:1", "http://", "://x"])
def test_a_bad_proxy_is_a_usage_error(bad):
    with pytest.raises(SystemExit) as caught:
        cli.main(["fetch", "zenodo:1", "--proxy", bad])
    assert caught.value.code == cli.EXIT_USAGE


def test_a_missing_ca_bundle_is_a_usage_error(tmp_path):
    with pytest.raises(SystemExit) as caught:
        cli.main(["fetch", "zenodo:1", "--ca-bundle", str(tmp_path / "no.pem")])
    assert caught.value.code == cli.EXIT_USAGE


def test_the_timeout_reaches_the_client():
    assert cli._client(_args("--timeout", "15")).timeout == 15.0


def test_a_bare_host_port_gets_http():
    assert cli._proxy("127.0.0.1:8899") == "http://127.0.0.1:8899"


def test_the_proxy_reaches_both_schemes():
    assert _proxies(cli._client(_args("--proxy", "http://127.0.0.1:8899"))) == {
        "http": "http://127.0.0.1:8899",
        "https": "http://127.0.0.1:8899",
    }


def test_no_proxy_leaves_no_proxy_handler(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://env.example:1")
    monkeypatch.setenv("HTTPS_PROXY", "http://env.example:1")

    client = cli._client(_args("--no-proxy"))

    assert not [h for h in client.opener.handlers if isinstance(h, urllib.request.ProxyHandler)]
    assert _proxies(client) == {}


def test_the_environment_is_used_by_default(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://env.example:1")
    monkeypatch.setenv("HTTP_PROXY", "http://env.example:1")

    proxies = _proxies(cli._client(_args()))
    assert proxies.get("https") == "http://env.example:1"


def test_an_explicit_proxy_beats_no_proxy(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://env.example:1")

    proxies = _proxies(cli._client(_args("--proxy", "http://own:1", "--no-proxy")))
    assert proxies == {"http": "http://own:1", "https": "http://own:1"}


def test_a_ca_bundle_adds_an_https_handler():
    bundle = Path(__file__).parent / "fixtures" / "ca.pem"

    client = cli._client(_args("--ca-bundle", str(bundle)))

    assert any(isinstance(h, urllib.request.HTTPSHandler) for h in client.opener.handlers)


def test_every_network_command_takes_the_flags(capsys):
    for argv in (
        ["fetch", "zenodo:1", "--timeout", "5"],
        ["manifest", "zenodo:1", "--timeout", "5"],
        ["verify", "m.json", "--timeout", "5"],
    ):
        args = cli.build_parser().parse_args(argv)
        assert args.timeout == 5.0

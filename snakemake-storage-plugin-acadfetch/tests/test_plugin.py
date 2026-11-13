"""The plugin, driven through Snakemake's own base classes.

Two things are worth testing here and nothing else is: that the classes satisfy
the interface Snakemake will call, and that the package is *discoverable* — which
depends entirely on its top-level module name, a detail no unit test of the code
would catch.
"""

from __future__ import annotations

import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import snakemake_storage_plugin_acadfetch as plugin
from snakemake_interface_common.logging import get_logger
from snakemake_interface_storage_plugins.io import IOCacheStorageInterface
from snakemake_interface_storage_plugins.registry import StoragePluginRegistry

PAYLOAD = b"snakemake payload" * 400
PLUGIN_ROOT = Path(__file__).resolve().parent.parent


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _body(self):
        return BODY

    def do_HEAD(self):  # noqa: N802
        body = self._body()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Last-Modified", "Wed, 21 Oct 2026 07:28:00 GMT")
        self.end_headers()

    def do_GET(self):  # noqa: N802
        body = self._body()
        start = 0
        wanted = self.headers.get("Range")
        if wanted and wanted.startswith("bytes="):
            start = int(wanted.split("=")[1].split("-")[0] or 0)
        chunk = body[start:]
        self.send_response(206 if start else 200)
        if start:
            self.send_header("Content-Range", f"bytes {start}-{len(body) - 1}/{len(body)}")
        self.send_header("Content-Length", str(len(chunk)))
        self.send_header("Last-Modified", "Wed, 21 Oct 2026 07:28:00 GMT")
        self.end_headers()
        self.wfile.write(chunk)
        self.wfile.flush()


BODY = PAYLOAD


class Missing(Handler):
    def do_HEAD(self):  # noqa: N802
        self.send_error(404)

    def do_GET(self):  # noqa: N802
        self.send_error(404)


class Truncating(Handler):
    """Declares the full length, delivers half of it once, then behaves."""

    cut = True
    ranges: list = []

    def do_GET(self):  # noqa: N802
        type(self).ranges.append(self.headers.get("Range"))
        if type(self).cut:
            type(self).cut = False
            self.send_response(200)
            self.send_header("Content-Length", str(len(BODY)))
            self.end_headers()
            self.wfile.write(BODY[: len(BODY) // 2])
            self.wfile.flush()
            self.close_connection = True
            return
        super().do_GET()


@pytest.fixture
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    srv.base = f"http://127.0.0.1:{srv.server_address[1]}"
    yield srv
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def missing_server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Missing)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    srv.base = f"http://127.0.0.1:{srv.server_address[1]}"
    yield srv
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def truncating_server():
    Truncating.cut = True
    Truncating.ranges = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Truncating)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    srv.base = f"http://127.0.0.1:{srv.server_address[1]}"
    srv.ranges = Truncating.ranges
    yield srv
    srv.shutdown()
    srv.server_close()


class FakeCache(IOCacheStorageInterface):
    """The five dictionaries Snakemake hands to ``inventory``.

    The base class exposes them as abstract properties, so a bare instance reads
    as ``None`` rather than failing — which is exactly the kind of silent
    interface drift this test should not paper over.
    """

    def __init__(self) -> None:
        self._exists_local: dict = {}
        self._exists_in_storage: dict = {}
        self._mtime: dict = {}
        self._size: dict = {}
        self._checksum: dict = {}

    @property
    def exists_local(self) -> dict:
        return self._exists_local

    @property
    def exists_in_storage(self) -> dict:
        return self._exists_in_storage

    @property
    def mtime(self) -> dict:
        return self._mtime

    @property
    def size(self) -> dict:
        return self._size

    @property
    def checksum(self) -> dict:
        return self._checksum


def make_object(tmp_path, url, **settings_kw):
    settings = plugin.StorageProviderSettings(**settings_kw)
    provider = plugin.StorageProvider(local_prefix=tmp_path, logger=get_logger(), settings=settings)
    return plugin.StorageObject(query=url, keep_local=True, retrieve=True, provider=provider)


# -- the interface ----------------------------------------------------------


def test_the_classes_implement_what_snakemake_abstracts():
    # Instantiation alone fails if an abstract method is missing, which is the
    # whole point: Snakemake constructs these, it never reads them.
    assert plugin.StorageObject.__abstractmethods__ == frozenset()
    assert plugin.StorageProvider.__abstractmethods__ == frozenset()


def test_snakemake_discovers_the_plugin_by_module_name(tmp_path):
    """Discovery is by top-level package name, not by an entry point."""
    sys.path.insert(0, str(PLUGIN_ROOT))
    try:
        registry = StoragePluginRegistry()
        registry.collect_plugins()
        assert "acadfetch" in registry.get_registered_plugins()
        registered = registry.get_plugin("acadfetch")
        assert registered.storage_provider is plugin.StorageProvider
    finally:
        sys.path.remove(str(PLUGIN_ROOT))


# -- queries ----------------------------------------------------------------


@pytest.mark.parametrize(
    "query",
    ["https://zenodo.org/records/1/files/a.tar.gz", "http://127.0.0.1:1/x"],
)
def test_a_file_url_is_a_valid_query(query):
    assert plugin.StorageProvider.is_valid_query(query).valid


@pytest.mark.parametrize("query", ["zenodo:123456", "10.5281/zenodo.1", "ftp://x/y", ""])
def test_a_bare_id_is_refused_with_the_reason(query):
    result = plugin.StorageProvider.is_valid_query(query)

    assert not result.valid
    assert "-w -" in result.reason, "the refusal has to name the way to get a URL"


# -- metadata ---------------------------------------------------------------


def test_exists_and_size_come_from_the_server(server, tmp_path):
    obj = make_object(tmp_path, server.base + "/a.bin")

    assert obj.exists() is True
    assert obj.size() == len(PAYLOAD)


def test_a_missing_url_is_not_available(missing_server, tmp_path):
    obj = make_object(tmp_path, missing_server.base + "/gone.bin")

    assert obj.exists() is False


def test_mtime_is_parsed_from_last_modified(server, tmp_path):
    obj = make_object(tmp_path, server.base + "/a.bin")

    # 2026-10-21T07:28:00Z, from the header the fixture sends.
    assert obj.mtime() == pytest.approx(1792567680.0, abs=1)


def test_an_object_without_a_date_is_treated_as_old(missing_server, tmp_path):
    obj = make_object(tmp_path, missing_server.base + "/gone.bin")

    assert obj.mtime() == 0.0


def test_inventory_records_the_three_facts(server, tmp_path):
    import asyncio

    obj = make_object(tmp_path, server.base + "/a.bin")
    cache = FakeCache()

    asyncio.run(obj.inventory(cache))

    name = obj.local_path()
    assert cache.exists_in_storage[name] is True
    assert cache.size[name] == len(PAYLOAD)


# -- transfer ---------------------------------------------------------------


def test_retrieve_object_lands_the_file_where_snakemake_expects_it(server, tmp_path):
    obj = make_object(tmp_path, server.base + "/a.bin")

    obj.retrieve_object()

    assert obj.local_path().read_bytes() == PAYLOAD
    assert obj.local_path().parent.is_dir()


def test_a_second_object_with_the_same_filename_gets_its_own_path(tmp_path):
    one = make_object(tmp_path, "https://a.example/data.csv")
    two = make_object(tmp_path, "https://b.example/data.csv")

    assert one.local_path() != two.local_path()
    assert "data.csv" in one.local_path().name


def test_a_failed_transfer_is_a_workflow_error(missing_server, tmp_path):
    from snakemake_interface_common.exceptions import WorkflowError

    obj = make_object(tmp_path, missing_server.base + "/gone.bin")

    with pytest.raises(WorkflowError) as caught:
        obj.retrieve_object()

    assert "acadfetch could not fetch" in str(caught.value)


def test_the_shared_client_is_reused_across_objects(tmp_path):
    one = make_object(tmp_path, "https://a.example/x")
    two = make_object(tmp_path, "https://b.example/y")

    assert one._client() is two._client(), "one bucket, not one per object"


def test_a_header_setting_reaches_the_request(tmp_path):
    obj = make_object(tmp_path, "https://a.example/x", header="Authorization: Bearer t")

    assert obj._client().headers["Authorization"] == "Bearer t"


def test_a_malformed_header_setting_is_refused(tmp_path):
    from snakemake_interface_common.exceptions import WorkflowError

    with pytest.raises(WorkflowError):
        make_object(tmp_path, "https://a.example/x", header="no colon here")._client()


def test_a_truncated_response_is_resumed_not_restarted(truncating_server, tmp_path):
    """The whole point of the plugin: one cut connection costs a chunk, not the file."""
    obj = make_object(tmp_path, truncating_server.base + "/a.bin")

    obj.retrieve_object()

    assert obj.local_path().read_bytes() == PAYLOAD
    ranges = [r for r in truncating_server.ranges if r]
    assert ranges, "the retry should have resumed with a Range request"

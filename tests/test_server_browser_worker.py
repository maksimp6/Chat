import json
import socket

import pytest

from browser.capabilities import BrowserAction
from browser.egress_proxy import connect_public
from browser.server_adapter import ServerBrowserAdapter
from browser.server_worker import Worker


class Driver:
    def __init__(self):
        self.calls = []

    def __call__(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        if path == "/session":
            return {"sessionId": "s"}
        if path.endswith("/title"):
            return "Example Domain"
        if method == "GET" and path.endswith("/url"):
            return "https://example.com/callback?token=SECRET#PRIVATE"
        return None


def test_worker_reuses_browser_and_does_not_return_redirect_credentials():
    driver = Driver()
    worker = Worker(driver)
    first = worker.execute({"action": "navigate", "target": "https://example.com/", "value": None})
    second = worker.execute({"action": "inspect", "target": "page", "value": None})
    assert (
        first
        == second
        == {"success": True, "data": {"title": "Example Domain", "origin": "https://example.com"}}
    )
    assert sum(path == "/session" for _, path, _ in driver.calls) == 1
    assert "SECRET" not in json.dumps(second)
    options = driver.calls[0][2]["capabilities"]["alwaysMatch"]["goog:chromeOptions"]["args"]
    assert "--no-sandbox" not in options
    assert "--proxy-bypass-list=<-loopback>" in options


@pytest.mark.parametrize(
    "target",
    [
        "file:///etc/passwd",
        "https://user:secret@example.com",
        "https://example.com?token=secret",
        "data:text/html,x",
    ],
)
def test_worker_rejects_sensitive_or_non_web_navigation_before_driver(target):
    driver = Driver()
    result = Worker(driver).execute({"action": "navigate", "target": target})
    assert not result["success"]
    assert driver.calls == []


@pytest.mark.parametrize("action", ["click", "fill", "screenshot"])
def test_raw_transport_cannot_self_approve_interactive_actions(action):
    driver = Driver()
    result = Worker(driver).execute({"action": action, "target": "button", "value": "secret"})
    assert not result["success"]
    assert driver.calls == []


def test_driver_failure_is_not_echoed():
    def failed(*args):
        raise RuntimeError("password=SECRET")

    result = Worker(failed).execute({"action": "inspect", "target": "page"})
    assert result == {"success": False, "error": "browser_execution_failed"}


def test_adapter_filters_unrecognized_fields_and_exceptions():
    adapter = ServerBrowserAdapter(
        lambda _: json.dumps(
            {"success": True, "data": {"title": "ok", "cookie": "SECRET"}, "password": "SECRET"}
        )
    )
    assert adapter.execute(BrowserAction("browser_cloud", "inspect", "page"))["data"] == {
        "title": "ok"
    }
    broken = ServerBrowserAdapter(lambda _: "invalid secret")
    assert (
        broken.execute(BrowserAction("browser_cloud", "inspect", "page"))["error"]
        == "server_browser_transport_failed"
    )


@pytest.mark.parametrize(
    "address", ["127.0.0.1", "169.254.169.254", "10.0.0.1", "::1", "192.168.1.1"]
)
def test_proxy_blocks_private_metadata_and_loopback(monkeypatch, address):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (address, 443))],
    )
    with pytest.raises(ValueError, match="non_public"):
        connect_public("example.com", 443)


def test_proxy_pins_checked_address_without_second_dns_lookup(monkeypatch):
    calls = []
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.215.14", 443))],
    )

    class Connection:
        def settimeout(self, value):
            pass

        def connect(self, value):
            calls.append(value)

    monkeypatch.setattr(socket, "socket", lambda *a: Connection())
    connect_public("example.com", 443)
    assert calls == [("93.184.215.14", 443)]


def test_worker_invalid_envelopes_and_assertion():
    worker = Worker(Driver())
    for value in [
        [],
        {"action": [], "target": "page"},
        {"action": "inspect", "target": None},
        {"action": "inspect", "target": "page", "approved": True},
    ]:
        assert not worker.execute(value)["success"]
    assert (
        worker.execute({"action": "assert_state", "target": "page", "value": "wrong"})["error"]
        == "title_mismatch"
    )
    worker.close()
    assert worker.session is None


def test_worker_http_boundary_sizes_json_and_not_found():
    import threading
    import urllib.request
    import urllib.error
    from browser.server_worker import create_server

    server = create_server(Worker(Driver()), ("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        for payload in [b"not-json", b"[]", b"x" * 8193]:
            with urllib.request.urlopen(
                urllib.request.Request(base + "/action", data=payload), timeout=2
            ) as response:
                assert not json.load(response)["success"]
        payload = json.dumps({"action": "inspect", "target": "page"}).encode()
        with urllib.request.urlopen(
            urllib.request.Request(base + "/action", data=payload), timeout=2
        ) as response:
            assert json.load(response)["success"]
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(urllib.request.Request(base + "/missing", data=b"{}"), timeout=2)
        assert error.value.code == 404
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


@pytest.mark.parametrize(
    "path", ["http://internal.test/", "file:///etc/passwd", "http://user:password@example.com/"]
)
def test_http_proxy_rejects_nonpublic_or_invalid_urls(monkeypatch, path):
    import threading
    from http.server import ThreadingHTTPServer
    from browser import egress_proxy

    monkeypatch.setattr(
        egress_proxy, "connect_public", lambda *a: (_ for _ in ()).throw(ValueError("private"))
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), egress_proxy.Proxy)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with socket.create_connection(server.server_address) as client:
            client.sendall(
                f"GET {path} HTTP/1.1\r\nHost: internal.test\r\nConnection: close\r\n\r\n".encode()
            )
            assert b"403" in client.recv(4096)
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


@pytest.mark.parametrize("method", ["GET", "CONNECT"])
def test_proxy_public_forwarding_and_connect_tunnel(monkeypatch, method):
    import threading
    from http.server import ThreadingHTTPServer
    from browser import egress_proxy

    upstream, fake_site = socket.socketpair()
    monkeypatch.setattr(egress_proxy, "connect_public", lambda *a: upstream)
    server = ThreadingHTTPServer(("127.0.0.1", 0), egress_proxy.Proxy)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with socket.create_connection(server.server_address, timeout=2) as client:
            client.settimeout(2)
            fake_site.settimeout(2)
            if method == "GET":
                client.sendall(
                    b"GET http://example.com/test?q=x HTTP/1.1\r\nHost: example.com\r\nProxy-Authorization: SECRET\r\nAccept: text/html\r\n\r\n"
                )
                forwarded = fake_site.recv(4096)
                assert b"GET /test?q=x" in forwarded
                assert b"SECRET" not in forwarded
                fake_site.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
                assert b"200" in client.recv(4096)
            else:
                client.sendall(b"CONNECT example.com:443 HTTP/1.1\r\nHost: example.com\r\n\r\n")
                assert b"200" in client.recv(4096)
                client.sendall(b"TLS bytes")
                assert fake_site.recv(4096) == b"TLS bytes"
                fake_site.sendall(b"reply")
                assert client.recv(4096) == b"reply"
        fake_site.close()
    finally:
        fake_site.close()
        server.shutdown()
        thread.join()
        server.server_close()


def test_adapter_rejects_write_and_invalid_results():
    for payload in ["x" * 16385, "[]", '{"success": "true"}']:
        assert not ServerBrowserAdapter(lambda _: payload).execute(
            BrowserAction("browser_cloud", "inspect", "page")
        )["success"]
    assert not ServerBrowserAdapter(lambda _: pytest.fail("transport called")).execute(
        BrowserAction("browser_cloud", "fill", "input", "secret")
    )["success"]
    adapter = ServerBrowserAdapter(lambda _: '{"success": false, "error": "SECRET"}')
    assert (
        adapter.execute(BrowserAction("browser_cloud", "inspect", "page"))["error"]
        == "server_browser_failed"
    )


def test_proxy_rejects_non_web_port_and_closes_failed_connection(monkeypatch):
    with pytest.raises(ValueError):
        connect_public("example.com", 22)
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.215.14", 443))],
    )
    closed = []

    class Failed:
        def settimeout(self, value):
            pass

        def connect(self, address):
            raise OSError("offline")

        def close(self):
            closed.append(True)

    monkeypatch.setattr(socket, "socket", lambda *a: Failed())
    with pytest.raises(OSError):
        connect_public("example.com", 443)
    assert closed == [True]


def test_driver_http_transport_and_safe_protocol_error(monkeypatch):
    import io
    from browser import server_worker

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()

    calls = []

    class Opener:
        def open(self, request, timeout):
            calls.append(request)
            return Response(b'{"value": {"ready": true}}')

    monkeypatch.setattr(server_worker.urllib.request, "build_opener", lambda *a: Opener())
    assert Worker._request("GET", "/status") == {"ready": True}
    assert calls[0].full_url == "http://127.0.0.1:9515/status"

    class ErrorOpener:
        def open(self, request, timeout):
            return Response(b'{"value": {"error": "unknown error", "message": "SECRET"}}')

    monkeypatch.setattr(server_worker.urllib.request, "build_opener", lambda *a: ErrorOpener())
    with pytest.raises(RuntimeError, match="^webdriver_failure$"):
        Worker._request("POST", "/session", {})


def test_request_cli_exit_code_and_size(monkeypatch, capsys):
    import io
    import sys
    from browser import server_worker

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    class Input:
        buffer = io.BytesIO(b"{}")

    class Opener:
        def open(self, *args, **kwargs):
            return Response(b'{"success": false, "error": "browser_execution_failed"}')

    monkeypatch.setattr(sys, "argv", ["worker", "request"])
    monkeypatch.setattr(sys, "stdin", Input())
    monkeypatch.setattr(server_worker.urllib.request, "build_opener", lambda *a: Opener())
    with pytest.raises(SystemExit) as failed:
        server_worker.main()
    assert failed.value.code == 1
    assert json.loads(capsys.readouterr().out)["success"] is False
    Input.buffer = io.BytesIO(b"x" * 8193)
    with pytest.raises(SystemExit, match="request_too_large"):
        server_worker.main()


def test_server_cli_starts_proxy_driver_and_cleans_up(monkeypatch):
    import sys
    from browser import server_worker

    events = []

    class Process:
        def terminate(self):
            events.append("terminate")

        def wait(self, timeout):
            events.append("wait")

    class Proxy:
        def shutdown(self):
            events.append("shutdown")

    monkeypatch.setattr(sys, "argv", ["worker", "serve"])
    monkeypatch.setattr(server_worker.Path, "mkdir", lambda *a, **k: None)
    monkeypatch.setattr(server_worker.os, "chmod", lambda *a: None)
    monkeypatch.setattr(server_worker.signal, "signal", lambda *a: None)
    monkeypatch.setattr(server_worker, "start_proxy", lambda: Proxy())
    monkeypatch.setattr(server_worker.subprocess, "Popen", lambda *a, **k: Process())
    monkeypatch.setattr(Worker, "_request", staticmethod(lambda *a: {"ready": True}))
    monkeypatch.setattr(server_worker, "serve", lambda: events.append("serve"))
    server_worker.main()
    assert events == ["serve", "shutdown", "terminate", "wait"]


def test_serve_closes_profile_when_server_stops(monkeypatch):
    from browser import server_worker

    events = []

    class FakeWorker:
        def close(self):
            events.append("close-profile")

    class Server:
        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            events.append("close-server")

    monkeypatch.setattr(server_worker, "Worker", FakeWorker)
    monkeypatch.setattr(server_worker, "create_server", lambda w: Server())
    with pytest.raises(KeyboardInterrupt):
        server_worker.serve()
    assert events == ["close-profile", "close-server"]


def test_proxy_connect_rejections_and_idle_timeout(monkeypatch):
    import threading
    from http.server import ThreadingHTTPServer
    from browser import egress_proxy

    monkeypatch.setattr(
        egress_proxy, "connect_public", lambda *a: (_ for _ in ()).throw(ValueError("private"))
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), egress_proxy.Proxy)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for target in ["internal.test:443", "user:password@example.com:443"]:
            with socket.create_connection(server.server_address) as client:
                client.sendall(f"CONNECT {target} HTTP/1.1\r\nHost: example.com\r\n\r\n".encode())
                assert b"403" in client.recv(4096)
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
    closed = []

    class Upstream:
        def close(self):
            closed.append(True)

    handler = object.__new__(egress_proxy.Proxy)
    handler.connection = object()
    monkeypatch.setattr(egress_proxy.select, "select", lambda *a: ([], [], []))
    handler.tunnel(Upstream())
    assert closed == [True]
    assert handler.close_connection


def test_proxy_start_binds_only_loopback(monkeypatch):
    from browser import egress_proxy

    events = []

    class Server:
        def serve_forever(self):
            pass

    server = Server()
    monkeypatch.setattr(
        egress_proxy,
        "ThreadingHTTPServer",
        lambda address, handler: events.append(address) or server,
    )

    class Thread:
        def __init__(self, target, daemon):
            assert daemon
            assert target == server.serve_forever

        def start(self):
            events.append("started")

    monkeypatch.setattr(egress_proxy.threading, "Thread", Thread)
    assert egress_proxy.start_proxy() is server
    assert events == [("127.0.0.1", 8899), "started"]


def test_server_cli_failed_startup_cleans_resources_and_term_handler(monkeypatch):
    import sys
    from browser import server_worker

    calls = []
    handlers = []

    class Process:
        def terminate(self):
            calls.append("terminate")

        def wait(self, timeout):
            calls.append("wait")

    class Proxy:
        def shutdown(self):
            calls.append("shutdown")

    monkeypatch.setattr(sys, "argv", ["worker", "serve"])
    monkeypatch.setattr(server_worker.Path, "mkdir", lambda *a, **k: None)
    monkeypatch.setattr(server_worker.os, "chmod", lambda *a: None)
    monkeypatch.setattr(
        server_worker.signal, "signal", lambda signum, handler: handlers.append(handler)
    )
    monkeypatch.setattr(server_worker, "start_proxy", lambda: Proxy())
    monkeypatch.setattr(server_worker.subprocess, "Popen", lambda *a, **k: Process())

    def unavailable(*args):
        raise OSError("offline")

    monkeypatch.setattr(Worker, "_request", staticmethod(unavailable))
    monkeypatch.setattr(server_worker.time, "sleep", lambda value: None)
    with pytest.raises(RuntimeError, match="driver_unavailable"):
        server_worker.main()
    assert calls == ["shutdown", "terminate", "wait"]
    with pytest.raises(SystemExit) as terminated:
        handlers[0](15, None)
    assert terminated.value.code == 0


def test_module_request_entrypoint_success(monkeypatch, capsys):
    import io
    import sys
    import runpy
    from browser import server_worker

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    class Input:
        buffer = io.BytesIO(b"{}")

    class Opener:
        def open(self, *args, **kwargs):
            return Response(b'{"success": true, "data": {"title": "Example"}}')

    monkeypatch.setattr(sys, "argv", ["worker", "request"])
    monkeypatch.setattr(sys, "stdin", Input())
    monkeypatch.setattr(server_worker.urllib.request, "build_opener", lambda *a: Opener())
    with pytest.warns(RuntimeWarning):
        runpy.run_module("browser.server_worker", run_name="__main__")
    assert json.loads(capsys.readouterr().out)["success"]

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

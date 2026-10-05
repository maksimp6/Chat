import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from browser.adapters import BrowserAdapterRegistry
from browser.capabilities import BrowserAction
from browser.emulator_adapter import EmulatorBrowserAdapter

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is required")

PAGE = b"""<html><head><title>Fixture</title></head><body>
<h1 id="title">Emulator fixture</h1>
<form action="/result" method="get"><input name="q"><button id="go" type="submit">Go</button></form>
</body></html>"""


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = PAGE if self.path == "/" else f"<p id='out'>{self.path}</p>".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture()
def site():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


@pytest.fixture()
def adapter():
    instance = EmulatorBrowserAdapter()
    yield instance
    instance.close()


def _run(registry, action, target, value=None):
    return registry.execute(BrowserAction("browser_local", action, target, value))


def test_emulator_drives_real_http_page_through_registry(site, adapter):
    registry = BrowserAdapterRegistry({"browser_local": adapter})

    assert _run(registry, "navigate", site + "/")["data"]["title"] == "Fixture"
    assert _run(registry, "assert_state", "#title", "fixture")["data"]["ok"] is True
    _run(registry, "fill", "input[name='q']", "alice")
    result = _run(registry, "click", "#go")
    assert result["success"] is True
    assert result["data"]["url"].endswith("/result?q=alice")
    assert "/result?q=alice" in _run(registry, "inspect", "#out")["data"]["text"]


def test_emulator_reports_unsupported_actions_without_crashing(adapter):
    registry = BrowserAdapterRegistry({"browser_local": adapter})
    result = _run(registry, "screenshot", "page")
    assert result["success"] is False
    assert "not supported" in result["error"]


def test_adapter_restarts_after_process_exit(site, adapter):
    adapter.execute(BrowserAction("browser_local", "navigate", site + "/"))
    adapter.close()
    adapter.close()
    result = adapter.execute(BrowserAction("browser_local", "navigate", site + "/"))
    assert result["success"] is True


def test_adapter_raises_when_emulator_dies(tmp_path):
    adapter = EmulatorBrowserAdapter(node="true")
    with pytest.raises(RuntimeError, match="exited unexpectedly"):
        adapter.execute(BrowserAction("browser_local", "inspect", "page"))


def test_adapter_treats_broken_pipe_as_exit():
    class _BrokenStdin:
        def write(self, _data):
            raise BrokenPipeError

    class _DeadProcess:
        stdin = _BrokenStdin()
        stdout = None

        def poll(self):
            return None

        def kill(self):
            pass

        def wait(self, timeout=None):
            return 0

    adapter = EmulatorBrowserAdapter()
    adapter._process = _DeadProcess()
    with pytest.raises(RuntimeError, match="exited unexpectedly"):
        adapter.execute(BrowserAction("browser_local", "inspect", "page"))
    assert adapter._process is None

import os
from pathlib import Path

from flask import Flask

from desktop import launcher


def test_desktop_host_is_loopback_only():
    assert launcher.SERVER_HOST == "127.0.0.1"


def test_configure_environment_uses_local_app_data(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("ALICE_DB_PATH", raising=False)
    monkeypatch.delenv("HOST", raising=False)
    monkeypatch.delenv("FLASK_DEBUG", raising=False)

    data_dir = launcher.configure_environment()

    assert data_dir == tmp_path / "Alice Pro"
    assert data_dir.is_dir()
    assert os.environ["ALICE_DB_PATH"] == str(data_dir / "alice_pro.db")
    assert os.environ["HOST"] == "127.0.0.1"
    assert os.environ["FLASK_DEBUG"] == "0"
    assert os.environ["ALICE_DESKTOP"] == "1"


def test_create_server_uses_ephemeral_loopback_port():
    app = Flask("desktop-test")

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    server = launcher.create_server(app)
    try:
        assert server.host == "127.0.0.1"
        assert server.server_port > 0
    finally:
        server.server_close()


def test_window_url_uses_server_port():
    class Server:
        server_port = 43210

    assert launcher.server_url(Server()) == "http://127.0.0.1:43210/"


def test_desktop_data_dir_honors_explicit_override(tmp_path, monkeypatch):
    target = tmp_path / "portable"
    monkeypatch.setenv("ALICE_DESKTOP_DATA_DIR", str(target))

    assert launcher.desktop_data_dir() == target.resolve()


def test_desktop_data_dir_falls_back_to_windows_home(monkeypatch, tmp_path):
    monkeypatch.delenv("ALICE_DESKTOP_DATA_DIR", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setattr(launcher.Path, "home", classmethod(lambda cls: tmp_path))

    assert launcher.desktop_data_dir() == tmp_path / "AppData" / "Local" / "Alice Pro"


def test_run_desktop_starts_window_and_shuts_server_down(monkeypatch):
    import sys
    import types

    events = []

    class FakeServer:
        server_port = 43123

        def serve_forever(self):
            events.append("serve")

        def shutdown(self):
            events.append("shutdown")

        def server_close(self):
            events.append("close")

    class FakeThread:
        def __init__(self, *, target, name, daemon):
            events.append(("thread", name, daemon))
            self.target = target

        def start(self):
            events.append("thread-start")
            self.target()

        def join(self, timeout=None):
            events.append(("thread-join", timeout))

    fake_server = FakeServer()
    fake_flask_app = object()
    fake_app_module = types.SimpleNamespace(app=fake_flask_app)
    fake_webview = types.SimpleNamespace(
        create_window=lambda *args, **kwargs: events.append(("window", args, kwargs)),
        start=lambda **kwargs: events.append(("webview-start", kwargs)),
    )

    monkeypatch.setattr(launcher, "configure_environment", lambda: events.append("configured"))
    monkeypatch.setattr(
        launcher,
        "create_server",
        lambda app: fake_server if app is fake_flask_app else None,
    )
    monkeypatch.setattr(launcher.threading, "Thread", FakeThread)
    monkeypatch.setitem(sys.modules, "app", fake_app_module)
    monkeypatch.setitem(sys.modules, "webview", fake_webview)

    launcher.run_desktop()

    assert "configured" in events
    assert "serve" in events
    assert any(item[0] == "window" for item in events if isinstance(item, tuple))
    assert ("webview-start", {"debug": False}) in events
    assert events[-3:] == ["shutdown", "close", ("thread-join", 5)]

import os
from pathlib import Path

from flask import Flask

import desktop_app


def test_desktop_host_is_loopback_only():
    assert desktop_app.SERVER_HOST == "127.0.0.1"


def test_configure_environment_uses_local_app_data(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("ALICE_DB_PATH", raising=False)
    monkeypatch.delenv("HOST", raising=False)
    monkeypatch.delenv("FLASK_DEBUG", raising=False)

    data_dir = desktop_app.configure_environment()

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

    server = desktop_app.create_server(app)
    try:
        assert server.host == "127.0.0.1"
        assert server.server_port > 0
    finally:
        server.server_close()


def test_window_url_uses_server_port():
    class Server:
        server_port = 43210

    assert desktop_app.server_url(Server()) == "http://127.0.0.1:43210/"

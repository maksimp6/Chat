import json

import pytest
from flask import Flask

from rdc_connection.pairing import install_rdc_pairing, verification_url
from short_token_auth import install_short_token_auth


def test_verification_url_expiry_and_origin(tmp_path):
    path = tmp_path / "handoff.json"
    value = {
        "expires_at": 101,
        "verification_uri_complete": "https://mcp.desktopcommander.app/device/verify?code=test",
    }
    path.write_text(json.dumps(value))
    assert verification_url(path, now=100) == value["verification_uri_complete"]
    with pytest.raises(ValueError):
        verification_url(path, now=101)
    for url in [
        "https://evil.example/",
        "https://mcp.desktopcommander.app.evil.example/",
        "http://mcp.desktopcommander.app/",
        "https://user@mcp.desktopcommander.app/",
        "https://mcp.desktopcommander.app:444/",
        "https://mcp.desktopcommander.app/\r\nX:evil",
    ]:
        value["verification_uri_complete"] = url
        path.write_text(json.dumps(value))
        with pytest.raises(ValueError):
            verification_url(path, now=100)


def test_route_requires_token_and_existing_live_handoff(monkeypatch, tmp_path):
    monkeypatch.setenv("ALICE_REQUIRE_SHORT_TOKEN", "1")
    monkeypatch.setenv("ALICE_SHORT_TOKEN", "test-secret")
    app = Flask(__name__)
    install_short_token_auth(app)
    install_rdc_pairing(app)
    client = app.test_client()
    assert client.get("/rdc").status_code == 401
    assert client.get("/test-secret/rdc").status_code == 503
    import time

    path = tmp_path / "handoff.json"
    url = "https://mcp.desktopcommander.app/device/verify?code=test"
    path.write_text(json.dumps({"expires_at": time.time() + 60, "verification_uri_complete": url}))
    monkeypatch.setenv("ALICE_RDC_PAIRING_FILE", str(path))
    response = client.get("/test-secret/rdc")
    assert response.status_code == 303
    assert response.headers["Location"] == url
    assert "test-secret" not in response.headers["Location"]
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Referrer-Policy"] == "no-referrer"


def test_disabled_auth_cannot_publish_pairing(monkeypatch):
    monkeypatch.delenv("ALICE_REQUIRE_SHORT_TOKEN", raising=False)
    app = Flask(__name__)
    install_rdc_pairing(app)
    assert app.test_client().get("/rdc").status_code == 401


@pytest.mark.parametrize("expiry", [True, "101", None, 701, 100, float("nan")])
def test_bad_expiry(tmp_path, expiry):
    path = tmp_path / "handoff.json"
    path.write_text(
        json.dumps(
            {"expires_at": expiry, "verification_uri_complete": "https://mcp.desktopcommander.app/"}
        )
    )
    with pytest.raises((ValueError, TypeError)):
        verification_url(path, now=100)


def test_oversized_handoff(tmp_path):
    path = tmp_path / "handoff.json"
    path.write_bytes(b" " * 4097)
    with pytest.raises(ValueError, match="oversized"):
        verification_url(path)

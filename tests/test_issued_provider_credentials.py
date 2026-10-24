"""Real HTTP import and encrypted journal reload; provider network is forbidden."""
from datetime import datetime, timedelta, timezone
import hashlib

import pytest


@pytest.fixture
def setup(monkeypatch, tmp_path):
    import requests
    import storage.db as db
    from agent_memory.client import close_memory_clients
    from flask import Flask
    import provider_accounts.credentials_routes as routes
    from provider_accounts import issued_credentials as issued
    close_memory_clients()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(db, "memory_file_path", lambda: tmp_path / "test.memory")
    monkeypatch.setenv("LOGOS_PROVIDER_CREDENTIAL_KEY", "fixture-encryption-key")
    monkeypatch.setenv("ALICE_PROVIDER_CREDENTIAL_KEY", "fixture-encryption-key")
    for prefix in ("LOGOS", "ALICE"):
        monkeypatch.delenv(prefix + "_PROVIDER_CREDENTIALS_TOKEN", raising=False)
        monkeypatch.setenv(prefix + "_REQUIRE_SHORT_TOKEN", "false")
    def forbidden(*args, **kwargs):
        raise AssertionError("Provider/model network request is forbidden")
    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    app = Flask(__name__); app.register_blueprint(routes.provider_credentials_bp)
    now = datetime.now(timezone.utc)
    payload = {"api_key": "fixture-issued-api-key", "project_id": "b" * 20, "provider_key_id": "c" * 20,
               "issued_at": now.isoformat(), "expires_at": (now + timedelta(hours=12)).isoformat()}
    yield app.test_client(), payload, db, issued, tmp_path
    close_memory_clients()


def test_http_import_survives_reload_and_is_used_by_credential_resolver(setup):
    client, payload, db, issued, path = setup
    from agent_memory.client import close_memory_clients
    from provider_accounts.credentials import resolve_client_credential
    assert client.get("/api/provider-credentials/yandex/issuance-status").json["encryption_ready"]
    response = client.post("/api/provider-credentials/yandex/issued", json=payload)
    assert response.status_code == 200, response.json
    assert response.json["storage_verified"] is True
    assert response.json["authorization_ok"] is False
    assert payload["api_key"] not in response.get_data(as_text=True)
    assert payload["api_key"].encode() not in (path / "test.memory").read_bytes()
    close_memory_clients()
    active = resolve_client_credential(None, db.get_store(), issued.decrypt_secret, provider="yandex")
    assert active.api_key == payload["api_key"]
    assert active.provider_key_id == payload["provider_key_id"]
    assert active.expires_at == datetime.fromisoformat(payload["expires_at"])
    status = client.get("/api/provider-credentials/yandex/issuance-status").json
    assert status["credential"]["fingerprint"] == hashlib.sha256(active.api_key.encode()).hexdigest()
    assert client.post("/api/provider-credentials/yandex/issued", json=payload).status_code == 200
    assert len(db.get_store().items(prefix="provider/credential/")) == 1


def test_different_key_cannot_replace_existing_key(setup):
    client, payload, db, issued, _ = setup
    assert client.post("/api/provider-credentials/yandex/issued", json=payload).status_code == 200
    changed = {**payload, "api_key": "another-fixture-key", "provider_key_id": "d" * 20}
    assert client.post("/api/provider-credentials/yandex/issued", json=changed).status_code == 409
    assert len(db.get_store().items(prefix="provider/credential/")) == 1


def test_encryption_is_required_before_storage(setup, monkeypatch):
    client, payload, db, issued, _ = setup
    monkeypatch.setattr(issued, "encrypt_secret", lambda value: value)
    assert client.post("/api/provider-credentials/yandex/issued", json=payload).status_code == 503
    assert not db.get_store().items(prefix="provider/credential/")


@pytest.mark.parametrize("fault", ["expired", "id", "json", "origin", "remote"])
def test_invalid_or_unauthorized_import_has_no_writes(setup, fault):
    client, payload, db, _, _ = setup
    kwargs = {"json": payload}
    if fault == "expired": payload["expires_at"] = "2020-01-01T00:00:00Z"
    elif fault == "id": payload["provider_key_id"] = "../../other"
    elif fault == "json": kwargs = {"data": payload}
    elif fault == "origin": kwargs["headers"] = {"Origin": "https://other.invalid"}
    elif fault == "remote": kwargs["environ_overrides"] = {"REMOTE_ADDR": "192.0.2.1"}
    response = client.post("/api/provider-credentials/yandex/issued", **kwargs)
    assert response.status_code in (400, 401, 403, 415)
    assert payload["api_key"] not in response.get_data(as_text=True)
    assert not db.get_store().items(prefix="provider/credential/")

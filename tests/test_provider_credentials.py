from datetime import datetime, timedelta, timezone

import pytest

from provider_accounts.credentials import (
    ExpiredCredentialError,
    NoActiveCredentialError,
    get_active_key,
    issue_window,
    rotation_needed,
    should_revoke,
)


class FakeDb:
    def __init__(self, row):
        self.row = row

    def get(self, key):
        if self.row is None:
            return None
        if key.startswith("provider/active/"):
            return self.row["id"]
        return {**self.row, "status": "active", "provider_key_id": self.row.get("yandex_key_id")}


def test_get_active_key_decrypts_active_unexpired_key():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    db = FakeDb(
        {
            "api_key_encrypted": "ciphertext",
            "id": 7,
            "yandex_key_id": "aje-key-7",
            "project_id": "project-1",
            "issued_at": now - timedelta(hours=11),
            "expires_at": now + timedelta(hours=1),
        }
    )
    assert get_active_key(db, lambda value: f"secret:{value}", now) == "secret:ciphertext"


def test_missing_active_key_fails():
    with pytest.raises(NoActiveCredentialError):
        get_active_key(FakeDb(None), lambda value: value)


def test_expired_active_key_fails():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    db = FakeDb(
        {
            "api_key_encrypted": "ciphertext",
            "id": 7,
            "yandex_key_id": "aje-key-7",
            "project_id": "project-1",
            "issued_at": now - timedelta(hours=13),
            "expires_at": now - timedelta(seconds=1),
        }
    )
    with pytest.raises(ExpiredCredentialError):
        get_active_key(db, lambda value: value, now)


def test_rotation_boundary_is_inclusive():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert not rotation_needed(now + timedelta(hours=1, seconds=1), now)
    assert rotation_needed(now + timedelta(hours=1), now)
    assert should_revoke(now, now)


def test_issue_window_is_12_hours():
    issued, expires = issue_window(datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert expires - issued == timedelta(hours=12)


def test_fingerprint_is_stable_and_non_secret():
    from provider_accounts.credentials import fingerprint_key

    secret = "sk-provider-secret"
    fingerprint = fingerprint_key(secret)
    assert len(fingerprint) == 64
    assert fingerprint != secret


def test_provider_credentials_are_isolated():
    import db
    from provider_accounts.credentials import replace_active_credential, get_active_credential

    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    store = db.get_store()
    for provider in ("yandex", "cloudru"):
        replace_active_credential(
            store,
            provider + "-secret",
            "project",
            lambda value: "cipher:" + value,
            provider,
            provider_key_id=provider + "-key",
            now=now,
        )
    for provider in ("yandex", "cloudru"):
        credential = get_active_credential(
            store, lambda value: value.removeprefix("cipher:"), now=now, provider=provider
        )
        assert credential.api_key == provider + "-secret"
        assert credential.provider_key_id == provider + "-key"


def test_create_schema_migrates_legacy_global_index():
    from memory_engine import MemoryStore, StoreError
    from tempfile import TemporaryDirectory
    from pathlib import Path

    with TemporaryDirectory() as root:
        path = Path(root) / "legacy.db"
        evidence = b"SQLite format 3\0legacy evidence"
        path.write_bytes(evidence)
        with pytest.raises(StoreError):
            MemoryStore(path)
        assert path.read_bytes() == evidence


def test_provider_health_metadata_has_no_secret():
    import db
    from cryptography.fernet import Fernet
    from provider_accounts.credentials import (
        record_health_check,
        replace_active_credential,
        list_provider_credentials,
    )

    fernet = Fernet(Fernet.generate_key())
    store = db.get_store()
    replace_active_credential(
        store,
        "secret-health",
        "project",
        lambda value: fernet.encrypt(value.encode()).decode(),
        "yandex",
    )
    record_health_check(store, "yandex", status="connected")
    row = list_provider_credentials(store)[0]
    assert row["fingerprint"]
    assert row["last_check_status"] == "connected"
    assert row["last_check_error"] is None
    assert "secret-health" not in str(row)
    assert "api_key_encrypted" not in row


def test_manual_provider_credential_does_not_fabricate_remote_key_id():
    import db
    from provider_accounts.credentials import replace_active_credential, list_provider_credentials

    credential = replace_active_credential(
        db.get_store(), "manual-secret", "", lambda value: "cipher", "cloudru"
    )
    row = list_provider_credentials(db.get_store())[0]
    assert row["id"] == credential.id
    assert row["provider_key_id"] is None
    assert row["fingerprint"]


def test_provider_credentials_update_returns_provider_auth_error(monkeypatch):
    from flask import Flask
    import provider_accounts.credentials_routes as routes

    class FakeClient:
        def validate_key(self, api_key):
            raise PermissionError("Yandex authorization failed (HTTP 401)")

    monkeypatch.setattr(routes, "_provider_client", lambda provider, project_id=None: FakeClient())
    app = Flask(__name__)
    app.register_blueprint(routes.provider_credentials_bp)
    app.testing = True

    with app.test_client() as client:
        response = client.put(
            "/api/provider-credentials",
            json={"yandex_api_key": "invalid-key", "yandex_project_id": "project-test"},
        )

    assert response.status_code == 401
    body = response.get_json()
    assert body["error"] == "authorization_failed"
    assert body["provider"] == "yandex"
    assert body["status"] == "invalid"


def test_provider_credentials_update_rejects_admin_auth_before_provider_validation(monkeypatch):
    import provider_accounts.credentials_routes as routes

    monkeypatch.setenv("ALICE_PROVIDER_CREDENTIALS_TOKEN", "expected-token")
    app = __import__("flask").Flask(__name__)
    app.register_blueprint(routes.provider_credentials_bp)
    app.testing = True

    with app.test_client() as client:
        response = client.put(
            "/api/provider-credentials",
            json={"yandex_api_key": "invalid-key", "yandex_project_id": "project-test"},
        )

    assert response.status_code == 401
    body = response.get_json()
    assert body["error"] == "provider_credential_admin_authentication_required"
    assert "provider API-key validation" in body["detail"]


def test_provider_credentials_update_accepts_yandex_static_key_without_remote_probe(monkeypatch):
    from flask import Flask
    import provider_accounts.credentials_routes as routes

    calls = []

    class FakeClient:
        def validate_key(self, api_key):
            calls.append(api_key)

    monkeypatch.setattr(routes, "_provider_client", lambda provider, project_id=None: FakeClient())
    monkeypatch.delenv("ALICE_PROVIDER_CREDENTIALS_TOKEN", raising=False)

    class FakeConn:
        def close(self):
            pass

    monkeypatch.setattr(routes, "get_store", lambda: FakeConn())
    monkeypatch.setattr(
        routes,
        "replace_active_credential",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(routes, "record_health_check", lambda *args, **kwargs: None)
    monkeypatch.setattr(routes, "provider_credentials_status", lambda: ({"ok": True}, 200))

    app = Flask(__name__)
    app.register_blueprint(routes.provider_credentials_bp)
    app.testing = True

    with app.test_client() as client:
        response = client.put(
            "/api/provider-credentials",
            json={"yandex_api_key": "static-yandex-key", "yandex_project_id": "project-test"},
        )

    assert response.status_code == 200
    assert calls == ["static-yandex-key"]


def test_provider_credentials_update_logs_storage_error_and_returns_detail(monkeypatch, caplog):
    from flask import Flask
    import provider_accounts.credentials_routes as routes

    class FakeClient:
        def validate_key(self, api_key):
            return None

    monkeypatch.setattr(routes, "_provider_client", lambda provider, project_id=None: FakeClient())
    monkeypatch.delenv("ALICE_PROVIDER_CREDENTIALS_TOKEN", raising=False)
    monkeypatch.setattr(
        routes, "get_store", lambda: (_ for _ in ()).throw(RuntimeError("sqlite exploded"))
    )

    app = Flask(__name__)
    app.register_blueprint(routes.provider_credentials_bp)
    app.testing = True

    with caplog.at_level("ERROR", logger="alice.provider_credentials"):
        with app.test_client() as client:
            response = client.put(
                "/api/provider-credentials",
                json={"yandex_api_key": "static-yandex-key", "yandex_project_id": "project-test"},
            )

    assert response.status_code == 503
    body = response.get_json()
    assert body["error"] == "credential_storage_failed"
    assert "не смог сохранить" in body["detail"]
    assert "sqlite exploded" in caplog.text

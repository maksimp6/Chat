from datetime import datetime, timezone

import base64

import provider_credentials_routes as routes


class FakeProvider:
    def validate_key(self, api_key):
        if api_key.startswith("bad"):
            raise PermissionError("unauthorized")

    def rotation_supported(self, provider_key_id):
        return bool(provider_key_id)


def test_status_never_returns_secret(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    import db
    db.DB_PATH = str(tmp_path / "status.db")
    db.init_db()
    monkeypatch.setenv(
        "ALICE_PROVIDER_CREDENTIAL_KEY",
        base64.urlsafe_b64encode(b"1" * 32).decode("ascii"),
    )
    monkeypatch.setattr(routes, "_provider_client", lambda provider: FakeProvider())
    monkeypatch.setattr(routes.config, "API_KEY", "", raising=False)
    monkeypatch.setattr(routes.config, "CLOUDRU_API_KEY", "", raising=False)

    conn = db.get_conn()
    from provider_credentials import replace_active_credential
    replace_active_credential(
        conn,
        "super-secret-yandex",
        "project-1",
        routes.encrypt_secret,
        "yandex",
        provider_key_id="yandex-id",
    )
    conn.close()

    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(routes.provider_credentials_bp)

    with app.test_client() as client:
        payload = client.get("/api/provider-credentials/status").get_json()

    serialized = str(payload)
    assert "super-secret-yandex" not in serialized
    assert "yandex" in serialized


def test_update_validates_before_persisting_and_clears_frontend_contract(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    import db
    db.DB_PATH = str(tmp_path / "update.db")
    db.init_db()
    monkeypatch.setenv(
        "ALICE_PROVIDER_CREDENTIAL_KEY",
        base64.urlsafe_b64encode(b"2" * 32).decode("ascii"),
    )
    monkeypatch.setattr(routes, "_provider_client", lambda provider: FakeProvider())
    monkeypatch.setattr(routes.config, "API_KEY", "", raising=False)
    monkeypatch.setattr(routes.config, "CLOUDRU_API_KEY", "", raising=False)

    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(routes.provider_credentials_bp)

    with app.test_client() as client:
        response = client.put(
            "/api/provider-credentials",
            json={
                "yandex_api_key": "good-yandex-secret",
                "cloudru_api_key": "good-cloudru-secret",
            },
        )

    assert response.status_code == 200
    payload = response.get_json()
    assert "good-yandex-secret" not in str(payload)
    assert "good-cloudru-secret" not in str(payload)
    statuses = {item["provider"]: item for item in payload["providers"]}
    assert statuses["yandex"]["status"] == "connected"
    assert statuses["cloudru"]["status"] == "connected"


def test_update_rejects_unauthorized_key_without_persisting(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    import db
    db.DB_PATH = str(tmp_path / "reject.db")
    db.init_db()
    monkeypatch.setenv(
        "ALICE_PROVIDER_CREDENTIAL_KEY",
        base64.urlsafe_b64encode(b"3" * 32).decode("ascii"),
    )
    monkeypatch.setattr(routes, "_provider_client", lambda provider: FakeProvider())
    monkeypatch.setattr(routes.config, "API_KEY", "", raising=False)
    monkeypatch.setattr(routes.config, "CLOUDRU_API_KEY", "", raising=False)

    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(routes.provider_credentials_bp)

    with app.test_client() as client:
        response = client.put(
            "/api/provider-credentials",
            json={"cloudru_api_key": "bad-cloudru-secret"},
        )

    assert response.status_code == 401
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT * FROM provider_credentials WHERE provider = 'cloudru'"
    ).fetchall()
    conn.close()
    assert rows == []


def test_provider_credentials_rejects_remote_without_admin_token(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    import db
    db.DB_PATH = str(tmp_path / "auth.db")
    db.init_db()
    monkeypatch.delenv("ALICE_PROVIDER_CREDENTIALS_TOKEN", raising=False)
    monkeypatch.setenv("ALICE_REQUIRE_SHORT_TOKEN", "false")

    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(routes.provider_credentials_bp)

    with app.test_client() as client:
        response = client.put(
            "/api/provider-credentials",
            json={"cloudru_api_key": "secret"},
            environ_base={"REMOTE_ADDR": "203.0.113.10"},
        )

    assert response.status_code == 401


def test_provider_credentials_accepts_explicit_admin_token(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    import db
    db.DB_PATH = str(tmp_path / "admin.db")
    db.init_db()
    monkeypatch.setenv("ALICE_PROVIDER_CREDENTIALS_TOKEN", "admin-test-token")
    monkeypatch.setenv(
        "ALICE_PROVIDER_CREDENTIALS_KEY",
        base64.urlsafe_b64encode(b"4" * 32).decode("ascii"),
    )
    monkeypatch.setattr(routes, "_provider_client", lambda provider: FakeProvider())
    monkeypatch.setattr(routes.config, "API_KEY", "", raising=False)
    monkeypatch.setattr(routes.config, "CLOUDRU_API_KEY", "", raising=False)
    monkeypatch.setenv(
        "ALICE_PROVIDER_CREDENTIAL_KEY",
        base64.urlsafe_b64encode(b"4" * 32).decode("ascii"),
    )

    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(routes.provider_credentials_bp)

    with app.test_client() as client:
        response = client.put(
            "/api/provider-credentials",
            headers={"Authorization": "Bearer admin-test-token"},
            json={"cloudru_api_key": "good-cloudru-secret"},
        )

    assert response.status_code == 200

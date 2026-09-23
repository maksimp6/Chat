from datetime import datetime, timezone

import base64

import provider_credentials_routes as routes


class FakeProvider:
    def validate_key(self, api_key):
        if api_key.startswith("bad"):
            raise PermissionError("unauthorized")

    def validate_runtime_access(self, api_key):
        self.validate_key(api_key)

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
            },
        )

    assert response.status_code == 200
    payload = response.get_json()
    assert "good-yandex-secret" not in str(payload)
    statuses = {item["provider"]: item for item in payload["providers"]}
    assert statuses["yandex"]["status"] == "connected"
    assert statuses["yandex"]["authorization_ok"] is True


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
            json={"yandex_api_key": "bad-yandex-secret"},
        )

    assert response.status_code == 401
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT * FROM provider_credentials WHERE provider = 'yandex'"
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
            json={"yandex_api_key": "secret"},
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
            json={"yandex_api_key": "good-yandex-secret"},
        )

    assert response.status_code == 200


def test_provider_credentials_status_check_requires_auth(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    import db
    db.DB_PATH = str(tmp_path / "check-auth.db")
    db.init_db()
    monkeypatch.delenv("ALICE_PROVIDER_CREDENTIALS_TOKEN", raising=False)
    monkeypatch.setenv("ALICE_REQUIRE_SHORT_TOKEN", "false")

    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(routes.provider_credentials_bp)

    with app.test_client() as client:
        response = client.post(
            "/api/provider-credentials/status/check",
            json={"provider": "cloudru"},
            environ_base={"REMOTE_ADDR": "203.0.113.11"},
        )

    assert response.status_code == 401


def test_cloudru_bootstrap_rejects_expired_master_key(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    import db
    db.DB_PATH = str(tmp_path / "expired-master.db")
    db.init_db()
    monkeypatch.setenv("ALICE_PROVIDER_CREDENTIALS_TOKEN", "admin-test-token")

    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(routes.provider_credentials_bp)

    with app.test_client() as client:
        response = client.post(
            "/api/provider-credentials/cloudru/bootstrap",
            headers={"Authorization": "Bearer admin-test-token"},
            data={
                "iam_key_id": "master-id",
                "iam_key_secret": "master-secret",
                "project_id": "project-1",
                "service_account_id": "sa-1",
                "expires_at": "2020-01-01T00:00:00Z",
            },
        )
    assert response.status_code == 401
    assert response.get_json()["error"] == "cloudru_iam_master_key_expired"


def test_cloudru_bootstrap_requires_existing_service_account(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    import db
    db.DB_PATH = str(tmp_path / "service-account-required.db")
    db.init_db()
    monkeypatch.setenv("ALICE_PROVIDER_CREDENTIALS_TOKEN", "admin-test-token")

    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(routes.provider_credentials_bp)

    with app.test_client() as client:
        response = client.post(
            "/api/provider-credentials/cloudru/bootstrap",
            headers={"Authorization": "Bearer admin-test-token"},
            data={
                "iam_key_id": "master-id",
                "iam_key_secret": "master-secret",
                "project_id": "project-1",
            },
        )

    assert response.status_code == 400
    assert response.get_json()["error"] == "service_account_id_required"


def test_cloudru_bootstrap_does_not_enumerate_service_accounts(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    import db
    db.DB_PATH = str(tmp_path / "bootstrap.db")
    db.init_db()
    monkeypatch.setenv("ALICE_PROVIDER_CREDENTIALS_TOKEN", "admin-test-token")
    monkeypatch.setenv(
        "ALICE_PROVIDER_CREDENTIAL_KEY",
        base64.urlsafe_b64encode(b"5" * 32).decode("ascii"),
    )

    class FakeManagement:
        def __init__(self, *, key_id, key_secret):
            self.key_id = key_id
            self.key_secret = key_secret

        def list_service_accounts(self):
            raise AssertionError("bootstrap must not enumerate service accounts")

        def create_api_key(self, **kwargs):
            assert kwargs["service_account_id"] == "sa-1"
            assert kwargs["products"] == ["foundation-models"]
            return {"id": "key-1", "secret": "runtime-secret"}

    class FakeProvider:
        def __init__(self, **kwargs):
            pass

        def validate_key(self, secret):
            assert secret == "runtime-secret"

    monkeypatch.setattr(routes, "CloudRuIamClient", FakeManagement)
    monkeypatch.setattr(routes, "CloudRuApiKeyProvider", FakeProvider)
    monkeypatch.setattr(routes, "save_cloudru_iam_credentials", lambda *args, **kwargs: None)
    monkeypatch.setattr(routes, "replace_active_credential", lambda *args, **kwargs: None)
    monkeypatch.setattr(routes, "record_health_check", lambda *args, **kwargs: None)

    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(routes.provider_credentials_bp)

    with app.test_client() as client:
        response = client.post(
            "/api/provider-credentials/cloudru/bootstrap",
            headers={"Authorization": "Bearer admin-test-token"},
            data={
                "iam_key_id": "master-id",
                "iam_key_secret": "master-secret",
                "project_id": "project-1",
                "service_account_id": "sa-1",
            },
        )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["service_account_id"] == "sa-1"
    assert payload["status"] == "connected"


def test_update_accepts_direct_cloudru_api_key(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    import db
    db.DB_PATH = str(tmp_path / "cloudru-direct.db")
    db.init_db()
    monkeypatch.setenv(
        "ALICE_PROVIDER_CREDENTIAL_KEY",
        base64.urlsafe_b64encode(b"6" * 32).decode("ascii"),
    )

    calls = []

    class CloudRuFake(FakeProvider):
        def validate_key(self, api_key):
            calls.append(api_key)
            super().validate_key(api_key)

    monkeypatch.setattr(routes, "_provider_client", lambda provider: CloudRuFake())
    monkeypatch.setattr(routes.config, "API_KEY", "", raising=False)

    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(routes.provider_credentials_bp)

    with app.test_client() as client:
        response = client.put(
            "/api/provider-credentials",
            json={"cloudru_api_key": "cloudru-runtime-secret"},
        )

    assert response.status_code == 200
    assert calls == ["cloudru-runtime-secret"]
    payload = response.get_json()
    assert "cloudru-runtime-secret" not in str(payload)
    statuses = {item["provider"]: item for item in payload["providers"]}
    assert statuses["cloudru"]["status"] == "connected"

    conn = db.get_conn()
    row = conn.execute(
        "SELECT provider, project_id, status FROM provider_credentials WHERE provider = 'cloudru'"
    ).fetchone()
    conn.close()
    assert row["provider"] == "cloudru"
    assert row["project_id"] == ""
    assert row["status"] == "active"


def test_update_accepts_yandex_and_cloudru_keys_together(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    import db
    db.DB_PATH = str(tmp_path / "both-providers.db")
    db.init_db()
    monkeypatch.setenv(
        "ALICE_PROVIDER_CREDENTIAL_KEY",
        base64.urlsafe_b64encode(b"7" * 32).decode("ascii"),
    )
    validated = []
    monkeypatch.setattr(
        routes,
        "_provider_client",
        lambda provider: type(
            "Provider",
            (),
            {"validate_key": lambda self, key: validated.append((provider, key)),
             "validate_runtime_access": lambda self, key: validated.append((provider, key)),
             "rotation_supported": lambda self, key_id: False},
        )(),
    )
    monkeypatch.setattr(routes.config, "API_KEY", "", raising=False)

    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(routes.provider_credentials_bp)

    with app.test_client() as client:
        response = client.put(
            "/api/provider-credentials",
            json={
                "yandex_api_key": "yandex-runtime-secret",
                "cloudru_api_key": "cloudru-runtime-secret",
            },
        )

    assert response.status_code == 200
    assert validated == [
        ("yandex", "yandex-runtime-secret"),
        ("cloudru", "cloudru-runtime-secret"),
    ]
    payload = response.get_json()
    serialized = str(payload)
    assert "yandex-runtime-secret" not in serialized
    assert "cloudru-runtime-secret" not in serialized

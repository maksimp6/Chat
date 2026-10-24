from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import cloud.cloudru.iam_routes as routes


def test_wizard_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("CLOUDRU_IAM_WIZARD_ENABLED", raising=False)
    monkeypatch.delenv("CLOUDRU_IAM_WIZARD_TOKEN", raising=False)
    assert routes._enabled() is False


def test_expiry_window_is_validated():
    now = datetime.now(timezone.utc)
    valid = routes._parse_expiry((now + timedelta(days=10)).isoformat())
    assert valid.endswith("Z")


def test_api_key_creation_route_stores_secret_once(monkeypatch, tmp_path):
    monkeypatch.setenv("CLOUDRU_IAM_WIZARD_ENABLED", "true")
    monkeypatch.setenv("CLOUDRU_IAM_WIZARD_TOKEN", "wizard-token")
    monkeypatch.setattr(routes, "secret_storage_ready", lambda: True)

    class FakeClient:
        def create_api_key(self, **kwargs):
            assert kwargs["service_account_id"] == "sa-1"
            return {"id": "key-1", "secret": "one-time-secret", "expires_at": kwargs["expires_at"]}

    monkeypatch.setattr(routes, "CloudRuIamClient", FakeClient)
    captured = {}

    def fake_store_secret(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(key_ref="cloudru_key-1", name=kwargs["name"])

    monkeypatch.setattr(routes, "store_secret", fake_store_secret)

    from flask import Flask

    app = Flask(__name__)
    app.register_blueprint(routes.cloudru_iam_bp)

    with app.test_client() as client:
        response = client.post(
            "/api/cloudru/iam/api-keys",
            headers={"Authorization": "Bearer wizard-token"},
            json={
                "service_account_id": "sa-1",
                "name": "Logos Pro",
                "products": ["monaas"],
                "confirm": True,
            },
        )

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    payload = response.get_json()
    assert payload["id"] == "key-1"
    assert payload["secret"] == "one-time-secret"
    assert captured["secret"] == "one-time-secret"
    assert captured["provider"] == "cloudru"
    assert captured["purpose"] == "api-key"


def test_wizard_without_a_configured_token_refuses_every_request(monkeypatch):
    monkeypatch.setenv("CLOUDRU_IAM_WIZARD_ENABLED", "true")
    monkeypatch.delenv("CLOUDRU_IAM_WIZARD_TOKEN", raising=False)
    from flask import Flask

    app = Flask(__name__)
    app.register_blueprint(routes.cloudru_iam_bp)
    with app.test_client() as client:
        response = client.get("/api/cloudru/iam/api-keys?service_account_id=sa-1")
    assert response.status_code == 401


def test_failed_secret_store_revokes_the_new_key(monkeypatch):
    monkeypatch.setenv("CLOUDRU_IAM_WIZARD_ENABLED", "true")
    monkeypatch.setenv("CLOUDRU_IAM_WIZARD_TOKEN", "wizard-token")
    revoked = []

    class FakeClient:
        def create_api_key(self, **kwargs):
            return {"id": "key-9", "secret": "s", "expires_at": kwargs["expires_at"]}

        def delete_api_key(self, *, api_key_id):
            revoked.append(api_key_id)

    def failing_store(**kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(routes, "CloudRuIamClient", FakeClient)
    monkeypatch.setattr(routes, "store_secret", failing_store)
    monkeypatch.setattr(routes, "secret_storage_ready", lambda: True)
    from flask import Flask

    app = Flask(__name__)
    app.register_blueprint(routes.cloudru_iam_bp)
    with app.test_client() as client:
        response = client.post(
            "/api/cloudru/iam/api-keys",
            headers={"Authorization": "Bearer wizard-token"},
            json={"service_account_id": "sa-1", "name": "n", "products": ["p"], "confirm": True},
        )
    assert response.status_code == 500
    assert revoked == ["key-9"]

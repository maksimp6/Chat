from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import cloudru_iam_routes as routes


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
    monkeypatch.delenv("CLOUDRU_IAM_WIZARD_TOKEN", raising=False)

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
        response = client.post("/api/cloudru/iam/api-keys", json={
            "service_account_id": "sa-1",
            "name": "Alice Pro",
            "products": ["monaas"],
            "confirm": True,
        })

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["id"] == "key-1"
    assert payload["secret"] == "one-time-secret"
    assert captured["secret"] == "one-time-secret"
    assert captured["provider"] == "cloudru"
    assert captured["purpose"] == "api-key"

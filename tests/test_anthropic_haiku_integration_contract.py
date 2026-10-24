"""RED acceptance at Alice's existing HTTP, persistence, and trace boundaries.

All credentials are synthetic; no test calls a live provider.
"""

import json

import pytest

from billing import aggregate_billing, build_ai_billing_item, settle_billing_to_treasury
from credential_crypto import decrypt_secret, encrypt_secret
from provider_credentials import (
    NoActiveCredentialError,
    bootstrap_credential,
    get_active_credential,
)


MODEL = "claude-haiku-5-5"
SECRET = "synthetic-anthropic-key-never-log"


@pytest.fixture
def isolated_store(monkeypatch, tmp_path):
    import db

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "haiku.db"))
    monkeypatch.setenv("ALICE_PROVIDER_CREDENTIAL_KEY", "synthetic-encryption-key")
    monkeypatch.setenv("ALICE_PROVIDER_CREDENTIALS_TOKEN", "synthetic-admin-token")
    db.init_db()
    return db


@pytest.fixture
def credential_client(isolated_store, monkeypatch):
    from flask import Flask
    import provider_credentials_routes as routes

    app = Flask(__name__)
    app.register_blueprint(routes.provider_credentials_bp)
    return app.test_client()


def admin_headers():
    return {"Authorization": "Bearer synthetic-admin-token"}


def test_authenticated_anthropic_setup_encrypts_key_and_preserves_yandex(
    isolated_store, credential_client, monkeypatch, caplog
):
    import provider_credentials_routes as routes

    conn = isolated_store.get_conn()
    bootstrap_credential(conn, "synthetic-yandex-key", "project", encrypt_secret)
    conn.close()
    validations = []

    class Provider:
        def validate_key(self, key):
            validations.append(key)

        def rotation_supported(self, _key_id):
            return False

    monkeypatch.setattr(routes, "_provider_client", lambda *args: Provider())
    response = credential_client.put(
        "/api/provider-credentials", headers=admin_headers(), json={"anthropic_api_key": SECRET}
    )
    assert response.status_code == 200
    assert validations == [SECRET]
    statuses = {item["provider"]: item for item in response.get_json()["providers"]}
    assert statuses["anthropic"]["authorization_ok"] is True
    conn = isolated_store.get_conn()
    try:
        stored = conn.execute(
            "SELECT api_key_encrypted FROM provider_credentials WHERE provider = 'anthropic'"
        ).fetchone()
        assert SECRET not in stored["api_key_encrypted"]
        assert get_active_credential(conn, decrypt_secret, provider="anthropic").api_key == SECRET
        assert get_active_credential(conn, decrypt_secret).api_key == "synthetic-yandex-key"
    finally:
        conn.close()
    assert SECRET not in response.get_data(as_text=True) + caplog.text


@pytest.mark.parametrize("failure,status", [(PermissionError, 401), (RuntimeError, 502)])
def test_failed_key_validation_never_persists_or_discloses_key(
    isolated_store, credential_client, monkeypatch, caplog, failure, status
):
    import provider_credentials_routes as routes

    class Provider:
        def validate_key(self, _key):
            raise failure(SECRET)

    monkeypatch.setattr(routes, "_provider_client", lambda *args: Provider())
    response = credential_client.put(
        "/api/provider-credentials", headers=admin_headers(), json={"anthropic_api_key": SECRET}
    )
    assert response.status_code == status
    conn = isolated_store.get_conn()
    try:
        assert not conn.execute(
            "SELECT id FROM provider_credentials WHERE provider = 'anthropic'"
        ).fetchall()
    finally:
        conn.close()
    assert SECRET not in response.get_data(as_text=True) + caplog.text


def test_remote_anthropic_setup_requires_admin_authentication(credential_client, monkeypatch):
    import provider_credentials_routes as routes

    def forbidden(*args):
        pytest.fail("unauthenticated request reached provider validation")

    monkeypatch.setattr(routes, "_provider_client", forbidden)
    response = credential_client.put(
        "/api/provider-credentials",
        json={"anthropic_api_key": SECRET},
        environ_base={"REMOTE_ADDR": "203.0.113.10"},
    )
    assert response.status_code == 401
    assert SECRET not in response.get_data(as_text=True)


def test_anthropic_setup_fails_closed_without_encryption(
    isolated_store, credential_client, monkeypatch
):
    import provider_credentials_routes as routes

    monkeypatch.delenv("ALICE_PROVIDER_CREDENTIAL_KEY")

    class Provider:
        def validate_key(self, _key):
            pass

    monkeypatch.setattr(routes, "_provider_client", lambda *args: Provider())
    response = credential_client.put(
        "/api/provider-credentials", headers=admin_headers(), json={"anthropic_api_key": SECRET}
    )
    assert response.status_code == 503
    conn = isolated_store.get_conn()
    try:
        assert not conn.execute(
            "SELECT id FROM provider_credentials WHERE provider = 'anthropic'"
        ).fetchall()
    finally:
        conn.close()


def test_degraded_catalog_keeps_haiku_selectable_alongside_yandex(monkeypatch):
    import app as application

    class OfflineDiscovery:
        def get_models(self, **kwargs):
            raise application.ModelDiscoveryError("synthetic offline")

    monkeypatch.setattr(application, "MODEL_DISCOVERY", OfflineDiscovery())
    response = application.app.test_client().get("/api/models")
    assert response.status_code == 200
    catalog = response.get_json()
    assert catalog["degraded"] is True
    assert catalog["text"][MODEL]["provider"] == "anthropic"
    assert "aliceai-llm" in catalog["text"]


def test_haiku_conversation_selection_survives_database_reload(isolated_store, monkeypatch):
    import app as application

    isolated_store.create_conversation("haiku-dialog", "Haiku", "aliceai-llm")
    isolated_store.create_conversation("yandex-dialog", "Yandex", "aliceai-llm")
    monkeypatch.setattr(application, "get_current_owner_id", lambda required=False: "owner")
    monkeypatch.setattr(application, "check_access", lambda *args: True)
    response = application.app.test_client().patch(
        "/api/conversations/haiku-dialog", json={"model": MODEL}
    )
    assert response.status_code == 200
    conversations = {row["id"]: row for row in isolated_store.get_conversations()}
    assert conversations["haiku-dialog"]["model"] == MODEL
    assert conversations["yandex-dialog"]["model"] == "aliceai-llm"


def test_trace_retains_anthropic_usage_and_usd_estimate_across_continuation():
    from invocation.context import InvocationContext
    from invocation.trace import create_invocation_trace

    context = InvocationContext("session-haiku", "dialog-haiku", "invoke-haiku", "trace-haiku")
    trace = create_invocation_trace(context)
    for step, usage in enumerate(
        [
            {"input_tokens": 1000, "output_tokens": 1000},
            {"input_tokens": 2000, "output_tokens": 1000},
        ],
        start=1,
    ):
        trace.add_api_request({"model": MODEL, "headers": {"x-api-key": SECRET}}, step)
        trace.add_response({"id": f"msg-{step}", "model": MODEL, "usage": usage}, step)
    data = trace.finalize()
    assert data["trace_id"] == context.trace_id
    assert data["context"]["invocation_id"] == context.invocation_id
    billing = data["billing"]
    assert billing["currency"] == "USD"
    assert billing["cost_status"] == "estimated"
    assert billing["total_cost"] == pytest.approx(0.0013)
    assert billing["input_tokens"] == 3000
    assert billing["output_tokens"] == 2000
    assert len(billing["items"]) == 2
    assert all(item["provider"] == "anthropic" for item in billing["items"])
    assert SECRET not in json.dumps(data)


def test_usd_billing_is_not_posted_as_rubles(monkeypatch):
    import treasury

    def forbidden(*args, **kwargs):
        pytest.fail("USD expense was posted without explicit currency conversion")

    monkeypatch.setattr(treasury, "record_expense", forbidden)
    result = settle_billing_to_treasury(
        {"currency": "USD", "cost_status": "calculated", "total_cost": 0.1, "trace_id": "haiku"},
        "owner",
    )
    assert result["status"] == "skipped"


def test_yandex_billing_remains_rub_per_thousand_tokens():
    item = build_ai_billing_item("aliceai-llm", {"input_tokens": 1000, "output_tokens": 1000}, 1)
    assert item["currency"] == "RUB"
    assert item["provider"] == "yandex_ai_studio"
    assert item["cost_status"] == "calculated"
    assert item["total_cost"] == pytest.approx(1.7)
    aggregate = aggregate_billing([item])
    assert aggregate["currency"] == "RUB"
    assert aggregate["total_cost"] == item["total_cost"]


@pytest.mark.parametrize("explicit_model", [True, False])
def test_chat_routes_to_haiku_and_preserves_invocation_trace(
    isolated_store, monkeypatch, explicit_model
):
    import app as application
    import mcp_routes as routes
    from trace_manager import get_current_trace

    isolated_store.create_conversation("haiku-chat", "Haiku", MODEL)
    selected = []
    observed_traces = []

    class Client:
        def ask_with_mcp(self, *, trace, model_key, **kwargs):
            assert model_key == MODEL
            assert trace is get_current_trace()
            observed_traces.append(trace.trace["trace_id"])
            response = {
                "id": "msg-chat",
                "model": MODEL,
                "usage": {"input_tokens": 12, "output_tokens": 5},
                "output": [
                    {"type": "message", "content": [{"type": "output_text", "text": "Haiku reply"}]}
                ],
            }
            trace.add_api_request({"model": MODEL})
            trace.add_response(response)
            return response

        def extract_reasoning_and_text(self, response):
            return "", "Haiku reply"

        def extract_usage(self, response):
            return response["usage"]

    def factory(model_key, **kwargs):
        selected.append(model_key)
        return Client()

    def forbidden(*args, **kwargs):
        pytest.fail("Haiku conversation was routed to Yandex")

    monkeypatch.setattr(routes, "create_model_client", factory, raising=False)
    monkeypatch.setattr(routes, "AliceClient", forbidden)
    request = {"conversation_id": "haiku-chat", "message": "hello"}
    if explicit_model:
        request["model"] = MODEL
    response = application.app.test_client().post("/api/chat", json=request)
    assert response.status_code == 200
    payload = response.get_json()
    assert selected == [MODEL]
    assert observed_traces == [payload["trace_id"]]
    assert payload["trace"]["context"]["invocation_id"] == payload["invocation_id"]
    assert payload["trace"]["billing"]["items"][0]["provider"] == "anthropic"
    assert get_current_trace() is None


@pytest.mark.parametrize("failure", [NoActiveCredentialError, PermissionError, TimeoutError])
def test_chat_provider_failure_never_uses_yandex_or_discloses_secret(
    isolated_store, monkeypatch, caplog, failure
):
    import app as application
    import mcp_routes as routes

    isolated_store.create_conversation("haiku-failure", "Haiku", MODEL)
    attempts = []

    def factory(model_key, **kwargs):
        attempts.append(model_key)
        raise failure(SECRET)

    def forbidden(*args, **kwargs):
        pytest.fail("Anthropic failure attempted a Yandex fallback")

    monkeypatch.setattr(routes, "create_model_client", factory, raising=False)
    monkeypatch.setattr(routes, "AliceClient", forbidden)
    response = application.app.test_client().post(
        "/api/chat", json={"conversation_id": "haiku-failure", "message": "hello", "model": MODEL}
    )
    assert 400 <= response.status_code < 600
    assert attempts == [MODEL]
    assert SECRET not in response.get_data(as_text=True) + caplog.text
    payload = response.get_json()
    assert payload["trace_id"] == payload["trace"]["trace_id"]

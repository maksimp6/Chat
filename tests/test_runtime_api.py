import json

import pytest

from api_contracts import validate_api_contract


@pytest.fixture
def client(tmp_path, monkeypatch):
    import db
    import runtime_migrations
    import session_manager
    import invocation_manager
    import app as app_module

    path = tmp_path / "api.db"
    monkeypatch.setattr(db, "DB_PATH", str(path))
    monkeypatch.setattr(session_manager, "get_conn", db.get_conn)
    monkeypatch.setattr(invocation_manager, "get_conn", db.get_conn)
    monkeypatch.setattr(runtime_migrations, "get_conn", db.get_conn)
    db.init_db()
    runtime_migrations.init_runtime_tables()
    app_module.app.config["TESTING"] = True
    return app_module.app.test_client()


def test_session_and_invocation_api(client):
    session_response = client.post("/api/sessions", json={"metadata": {"profile": "assistant"}})
    assert session_response.status_code == 201
    session = session_response.get_json()
    validate_api_contract("runtime.session.response", session)

    invocation_response = client.post(
        f"/api/sessions/{session['id']}/invocations",
        json={"conversation_id": "conversation-1"},
    )
    assert invocation_response.status_code == 201
    invocation = invocation_response.get_json()
    validate_api_contract("runtime.invocation.create.response", invocation)

    assert invocation["session_id"] == session["id"]
    assert invocation["conversation_id"] == "conversation-1"
    assert invocation["invocation_id"]
    assert invocation["trace_id"]

    loaded = client.get(f"/api/invocations/{invocation['invocation_id']}")
    assert loaded.status_code == 200
    loaded_payload = loaded.get_json()
    validate_api_contract("runtime.invocation.response", loaded_payload)
    assert loaded_payload["status"] == "created"

    session_detail_response = client.get(f"/api/sessions/{session['id']}")
    assert session_detail_response.status_code == 200
    validate_api_contract("runtime.session.detail.response", session_detail_response.get_json())


def test_status_endpoint_is_safe_and_not_found_is_stable(client):
    session = client.post("/api/sessions", json={"metadata": {}}).get_json()
    invocation = client.post(
        f"/api/sessions/{session['id']}/invocations",
        json={
            "conversation_id": "conversation-status",
            "metadata": {"authorization": "secret", "kind": "test"},
        },
    ).get_json()

    response = client.get(f"/api/invocations/{invocation['invocation_id']}/status")
    assert response.status_code == 200
    payload = response.get_json()
    validate_api_contract("runtime.invocation.status.response", payload)
    assert payload["status"] == "created"
    assert payload["session_id"] == session["id"]
    assert payload["conversation_id"] == "conversation-status"
    assert "secret" not in json.dumps(payload)

    missing = client.get("/api/invocations/does-not-exist/status")
    assert missing.status_code == 404
    missing_payload = missing.get_json()
    validate_api_contract("runtime.error.basic", missing_payload)
    assert missing_payload == {"error": "invocation_not_found"}


def test_trace_endpoint_returns_correlated_persisted_trace(client):
    session = client.post("/api/sessions", json={}).get_json()
    invocation = client.post(
        f"/api/sessions/{session['id']}/invocations",
        json={"conversation_id": "conversation-trace"},
    ).get_json()

    import db
    from invocation_context import InvocationContext
    from invocation_trace import create_invocation_trace

    context = InvocationContext(
        session_id=invocation["session_id"],
        conversation_id=invocation["conversation_id"],
        invocation_id=invocation["invocation_id"],
        trace_id=invocation["trace_id"],
    )
    trace = create_invocation_trace(context)
    trace.set_request({"authorization": "top-secret", "message": "hello"})
    trace.add_response(
        {
            "id": "resp-1",
            "status": "completed",
            "headers": {"authorization": "secret"},
            "output": [{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}],
        }
    )
    trace_data = trace.finalize()

    conn = db.get_conn()
    conn.execute(
        "INSERT INTO conversations (id, title, model, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        ("conversation-trace", "Trace", "aliceai-llm", 1, 1),
    )
    conn.execute(
        "INSERT INTO messages (conversation_id, role, content, created_at, trace_json) VALUES (?, ?, ?, ?, ?)",
        ("conversation-trace", "assistant", "ok", 1, json.dumps(trace_data)),
    )
    conn.commit()
    conn.close()

    response = client.get(f"/api/invocations/{invocation['invocation_id']}/trace")
    assert response.status_code == 200
    payload = response.get_json()
    validate_api_contract("runtime.invocation.trace.response", payload)
    assert payload["context"]["invocation_id"] == invocation["invocation_id"]
    assert payload["context"]["session_id"] == invocation["session_id"]
    assert payload["context"]["conversation_id"] == "conversation-trace"
    serialized = json.dumps(payload)
    assert "top-secret" not in serialized
    assert "secret" not in serialized

    missing = client.get("/api/invocations/does-not-exist/trace")
    assert missing.status_code == 404
    assert missing.get_json() == {"error": "invocation_not_found"}


def test_runtime_api_rejects_request_contract_drift(client):
    unknown_session_field = client.post(
        "/api/sessions",
        json={"metadata": {}, "unexpected": True},
    )
    assert unknown_session_field.status_code == 400
    payload = unknown_session_field.get_json()
    validate_api_contract("runtime.error.invalid_request", payload)
    assert payload["error"] == "invalid_request"
    assert payload["message"] == "request payload does not match API contract"
    assert "unexpected fields: unexpected" not in str(payload)

    wrong_metadata_type = client.post("/api/sessions", json={"metadata": "not-an-object"})
    assert wrong_metadata_type.status_code == 400
    validate_api_contract("runtime.error.invalid_request", wrong_metadata_type.get_json())

    session = client.post("/api/sessions", json={}).get_json()
    missing_conversation = client.post(f"/api/sessions/{session['id']}/invocations", json={})
    assert missing_conversation.status_code == 400
    payload = missing_conversation.get_json()
    validate_api_contract("runtime.error.invalid_request", payload)
    assert payload["message"] == "request payload does not match API contract"
    assert "missing required fields: conversation_id" not in str(payload)

    blank_conversation = client.post(
        f"/api/sessions/{session['id']}/invocations",
        json={"conversation_id": "   "},
    )
    assert blank_conversation.status_code == 400
    validate_api_contract("runtime.error.invalid_request", blank_conversation.get_json())

    extra_invocation_field = client.post(
        f"/api/sessions/{session['id']}/invocations",
        json={"conversation_id": "conv", "extra": 1},
    )
    assert extra_invocation_field.status_code == 400
    validate_api_contract("runtime.error.invalid_request", extra_invocation_field.get_json())

    missing_session = client.post(
        "/api/sessions/does-not-exist/invocations",
        json={"conversation_id": "conv"},
    )
    assert missing_session.status_code == 404
    payload = missing_session.get_json()
    validate_api_contract("runtime.error.message", payload)
    assert payload == {"error": "session_not_found", "message": "session not found"}


def test_session_profile_routes_obey_named_contracts(client):
    response = client.get("/api/session-profiles")
    assert response.status_code == 200
    payload = response.get_json()
    validate_api_contract("runtime.profile.list.response", payload)
    assert payload["profiles"]

    profile_id = payload["profiles"][0]["id"]
    profile_response = client.get(f"/api/session-profiles/{profile_id}")
    assert profile_response.status_code == 200
    validate_api_contract("runtime.profile.response", profile_response.get_json())

    clone_response = client.post(
        f"/api/session-profiles/{profile_id}/clone",
        json={"name": "Strict clone"},
    )
    assert clone_response.status_code == 201
    validate_api_contract("runtime.profile.clone.response", clone_response.get_json())

    blank_name = client.post(
        f"/api/session-profiles/{profile_id}/clone",
        json={"name": "   "},
    )
    assert blank_name.status_code == 400
    validate_api_contract("runtime.error.invalid_request", blank_name.get_json())

    extra_field = client.post(
        f"/api/session-profiles/{profile_id}/clone",
        json={"name": "Strict clone", "extra": True},
    )
    assert extra_field.status_code == 400
    validate_api_contract("runtime.error.invalid_request", extra_field.get_json())


def test_runtime_not_found_responses_have_exact_error_contract(client):
    missing_session = client.get("/api/sessions/missing")
    assert missing_session.status_code == 404
    payload = missing_session.get_json()
    validate_api_contract("runtime.error.basic", payload)
    assert payload == {"error": "session_not_found"}

    missing_profile = client.get("/api/session-profiles/missing")
    assert missing_profile.status_code == 404
    payload = missing_profile.get_json()
    validate_api_contract("runtime.error.basic", payload)
    assert payload == {"error": "profile_not_found"}

    missing_invocation = client.get("/api/invocations/missing")
    assert missing_invocation.status_code == 404
    payload = missing_invocation.get_json()
    validate_api_contract("runtime.error.basic", payload)
    assert payload == {"error": "invocation_not_found"}

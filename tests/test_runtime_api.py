import json

import pytest


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

    invocation_response = client.post(
        f"/api/sessions/{session['id']}/invocations",
        json={"conversation_id": "conversation-1"},
    )
    assert invocation_response.status_code == 201
    invocation = invocation_response.get_json()

    assert invocation["session_id"] == session["id"]
    assert invocation["conversation_id"] == "conversation-1"
    assert invocation["invocation_id"]
    assert invocation["trace_id"]

    loaded = client.get(f"/api/invocations/{invocation['invocation_id']}")
    assert loaded.status_code == 200
    assert loaded.get_json()["status"] == "created"


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
    assert payload["status"] == "created"
    assert payload["session_id"] == session["id"]
    assert payload["conversation_id"] == "conversation-status"
    assert "secret" not in json.dumps(payload)

    missing = client.get("/api/invocations/does-not-exist/status")
    assert missing.status_code == 404
    assert missing.get_json() == {"error": "invocation_not_found"}


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
    assert payload["context"]["invocation_id"] == invocation["invocation_id"]
    assert payload["context"]["session_id"] == invocation["session_id"]
    assert payload["context"]["conversation_id"] == "conversation-trace"
    serialized = json.dumps(payload)
    assert "top-secret" not in serialized
    assert "secret" not in serialized

    missing = client.get("/api/invocations/does-not-exist/trace")
    assert missing.status_code == 404
    assert missing.get_json() == {"error": "invocation_not_found"}

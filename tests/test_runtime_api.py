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

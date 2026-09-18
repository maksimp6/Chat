import json
import sqlite3

import pytest
from flask import Flask

import local_agent_gateway


@pytest.fixture()
def client(monkeypatch, tmp_path):
    db_path = tmp_path / "alice.db"

    def connection():
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn

    monkeypatch.setattr(local_agent_gateway, "get_conn", connection)
    monkeypatch.setenv("ALICE_LOCAL_AGENT_BOOTSTRAP_TOKEN", "bootstrap")

    app = Flask(__name__)
    app.register_blueprint(local_agent_gateway.local_agent_bp)
    local_agent_gateway.init_local_agent_tables()
    app.testing = True
    return app.test_client()


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def register(client, agent_id="android-test"):
    response = client.post(
        "/api/local-agents/register",
        json={
            "agent_id": agent_id,
            "name": "Test phone",
            "capabilities": ["local.tools"],
        },
        headers=auth("bootstrap"),
    )
    assert response.status_code == 201
    return response.get_json()


def test_registration_requires_bootstrap_token(client):
    response = client.post(
        "/api/local-agents/register",
        json={"agent_id": "android-test"},
    )
    assert response.status_code == 401


def test_registration_returns_runtime_token_and_descriptor(client):
    body = register(client)

    assert body["agent"]["id"] == "android-test"
    assert body["agent"]["capabilities"] == ["local.tools"]
    assert body["token"]


def test_poll_claim_and_result_are_authenticated(client):
    body = register(client)
    token = body["token"]

    job_id = local_agent_gateway.enqueue_local_tool_job(
        "android-test",
        "demo.echo",
        {"value": 1},
    )

    poll = client.get(
        "/api/local-agents/android-test/poll",
        headers=auth(token),
    )
    assert poll.status_code == 200

    job = poll.get_json()["job"]
    assert job["id"] == job_id
    assert job["arguments"] == {"value": 1}
    assert job["lease_until"] > 0

    result = client.post(
        f"/api/local-agents/android-test/jobs/{job_id}/result",
        json={
            "status": "completed",
            "result": {
                "success": True,
                "data": {"value": 1},
                "error": None,
            },
        },
        headers=auth(token),
    )
    assert result.status_code == 200

    second_poll = client.get(
        "/api/local-agents/android-test/poll",
        headers=auth(token),
    )
    assert second_poll.status_code == 200
    assert second_poll.get_json()["job"] is None


def test_poll_rejects_invalid_agent_token(client):
    register(client)

    response = client.get(
        "/api/local-agents/android-test/poll",
        headers=auth("wrong"),
    )
    assert response.status_code == 401


def test_duplicate_result_is_idempotent(client):
    body = register(client)
    token = body["token"]
    job_id = local_agent_gateway.enqueue_local_tool_job(
        "android-test",
        "demo.echo",
        {"value": 1},
    )

    client.get(
        "/api/local-agents/android-test/poll",
        headers=auth(token),
    )
    payload = {"status": "failed", "result": {"error": "boom"}}

    first = client.post(
        f"/api/local-agents/android-test/jobs/{job_id}/result",
        json=payload,
        headers=auth(token),
    )
    second = client.post(
        f"/api/local-agents/android-test/jobs/{job_id}/result",
        json=payload,
        headers=auth(token),
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.get_json()["idempotent"] is True


def test_health_does_not_expose_runtime_tokens(client):
    register(client)

    response = client.get("/api/local-agents/health", headers=auth("bootstrap"))
    assert response.status_code == 200
    assert "token" not in json.dumps(response.get_json())

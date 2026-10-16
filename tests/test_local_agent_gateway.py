import json

import pytest
from flask import Flask

import agent_runtime.local_gateway as local_agent_gateway


@pytest.fixture()
def client(monkeypatch, tmp_path):
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


@pytest.mark.parametrize("endpoint", ["heartbeat", "poll", "jobs/job/result"])
@pytest.mark.parametrize(
    "failure,status,code",
    [
        (LookupError, 404, "agent_not_found"),
        (PermissionError, 401, "local_agent_unauthorized"),
    ],
)
def test_agent_errors_never_expose_internal_exception_details(
    client, monkeypatch, endpoint, failure, status, code
):
    secret_detail = "/private/runtime/path: credential-like-internal-detail"

    def reject(_agent_id):
        raise failure(secret_detail)

    monkeypatch.setattr(local_agent_gateway, "_require_agent", reject)
    if endpoint == "poll":
        response = client.get(f"/api/local-agents/android-test/{endpoint}")
    else:
        response = client.post(
            f"/api/local-agents/android-test/{endpoint}", json={"status": "completed"}
        )
    assert response.status_code == status
    assert response.get_json() == {"error": code}
    assert secret_detail not in response.get_data(as_text=True)


@pytest.mark.parametrize("endpoint", ["register", "health"])
def test_bootstrap_errors_use_fixed_public_code(client, monkeypatch, endpoint):
    def reject():
        raise PermissionError("private internal credential detail")

    monkeypatch.setattr(local_agent_gateway, "_require_bootstrap", reject)
    response = (
        client.post("/api/local-agents/register", json={})
        if endpoint == "register"
        else client.get("/api/local-agents/health")
    )
    assert response.status_code == 401
    assert response.get_json() == {"error": "local_agent_unauthorized"}

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


@pytest.mark.parametrize(
    ("status", "payload", "success", "error"),
    [
        ("completed", {"success": True, "data": {"value": 7}}, True, None),
        ("completed", {"success": False, "error": "hidden"}, False, "local_agent_tool_failed"),
        ("failed", {"success": True, "data": "hidden"}, False, "local_agent_job_failed"),
        ("completed", {"success": "false"}, False, "local_agent_result_invalid"),
    ],
)
def test_executor_uses_real_gateway_with_trace(
    client, monkeypatch, status, payload, success, error
):
    """Do not replace local_agent_gateway in sys.modules: that hid the broken import."""
    from types import SimpleNamespace

    import local_agent_results
    from universal_tool_platform import (
        UniversalToolCall,
        UniversalToolDefinition,
        UniversalToolExecutor,
    )

    token = register(client)["token"]
    definition = UniversalToolDefinition(
        name="demo.echo",
        description="Synthetic remote tool",
        input_schema={"type": "object", "properties": {"value": {"type": "integer"}}},
        output_schema={"type": "object"},
        executor={"type": "local_agent", "agent_id": "android-test", "timeout_seconds": 1},
        metadata={"trace_redact_arguments": ["value"]},
    )
    registry = SimpleNamespace(get_universal_definition=lambda _name: definition)
    executor = UniversalToolExecutor(registry)
    observed_jobs = []

    class TraceRecorder:
        """Trace-interface test double; the executor and gateway remain real."""

        def __init__(self):
            self.trace = {"tool_calls": []}

        def track_tool_execution(self, name, arguments, func, **kwargs):
            result = func()
            self.trace["tool_calls"].append(
                {"name": name, "arguments": kwargs["trace_arguments"], "result": result}
            )
            return result

    def complete_during_wait(_seconds):
        response = client.get("/api/local-agents/android-test/poll", headers=auth(token))
        assert response.status_code == 200
        job = response.get_json()["job"]
        observed_jobs.append(job)
        assert job["arguments"] == {"value": 7}
        assert job["trace_id"] == "trace-remote-test"
        assert job["invocation_id"] == "invocation-remote-test"
        assert job["metadata"]["request_id"] == "request-remote-test"
        assert "execution_trace" not in job["metadata"]
        response = client.post(
            f"/api/local-agents/android-test/jobs/{job['id']}/result",
            json={"status": status, "result": payload, "error": {"message": "hidden"}},
            headers=auth(token),
        )
        assert response.status_code == 200

    monkeypatch.setattr(local_agent_results.time, "sleep", complete_during_wait)
    trace = TraceRecorder()
    result = executor.execute_with_trace(
        UniversalToolCall(
            "demo.echo",
            {"value": 7},
            approved=True,
            trace_id="trace-remote-test",
            invocation_id="invocation-remote-test",
            metadata={"request_id": "request-remote-test"},
        ),
        trace,
    )
    assert result["success"] is success
    assert result["error"] == error
    assert len(observed_jobs) == 1
    assert result["metadata"]["job_id"] == observed_jobs[0]["id"]
    assert result["metadata"]["trace_id"] == "trace-remote-test"
    assert result["metadata"]["invocation_id"] == "invocation-remote-test"
    assert len(trace.trace["tool_calls"]) == 1
    assert trace.trace["tool_calls"][0]["arguments"] == {"value": "<redacted>"}
    assert "hidden" not in json.dumps(result)
    if success:
        assert result["data"] == {"value": 7}


@pytest.mark.parametrize("gate", ["approval", "authorization", "policy"])
def test_denied_remote_call_never_enters_real_queue(client, gate):
    from types import SimpleNamespace

    from universal_tool_platform import (
        UniversalToolCall,
        UniversalToolDefinition,
        UniversalToolExecutor,
    )

    register(client)
    definition = UniversalToolDefinition(
        name="demo.echo",
        description="Synthetic remote tool",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        executor={"type": "local_agent", "agent_id": "android-test"},
    )
    executor = UniversalToolExecutor(
        SimpleNamespace(get_universal_definition=lambda _name: definition)
    )
    hooks = {}
    if gate == "authorization":
        hooks["authorize"] = lambda *_args: False
    elif gate == "policy":
        hooks["policy"] = lambda *_args: False
    result = executor.execute(
        UniversalToolCall("demo.echo", {}, approved=gate != "approval"), **hooks
    )
    assert result["success"] is False
    conn = local_agent_gateway.get_conn()
    try:
        assert conn.execute("SELECT COUNT(*) FROM local_agent_jobs").fetchone()[0] == 0
    finally:
        conn.close()

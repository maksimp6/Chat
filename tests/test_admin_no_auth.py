"""The shared deployment serves administration and local agents without login."""

import json
from flask import Flask
import pytest

from agent_memory.client import close_memory_clients
import agent_runtime.local_gateway as gateway
import agent_runtime.local_tool_agent as agent_client
import cloud.cloudru.iam_routes as iam
import plugin_system.routes as plugins
import provider_accounts.credentials_routes as credentials
import provider_accounts.quota_routes as quotas
import remote_runtime.environment_routes as environments


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("LOGOS_MEMORY_PATH", str(tmp_path / "logos.memory"))
    for setting in (
        "LOGOS_PROVIDER_CREDENTIALS_TOKEN",
        "LOGOS_QUOTA_ADMIN_TOKEN",
        "LOGOS_PLUGIN_ADMIN_TOKEN",
        "LOGOS_LOCAL_AGENT_BOOTSTRAP_TOKEN",
        "CLOUDRU_IAM_WIZARD_TOKEN",
    ):
        monkeypatch.setenv(setting, "unused-legacy-token")
    app = Flask(__name__)
    app.testing = True
    for blueprint in (
        gateway.local_agent_bp, credentials.provider_credentials_bp,
        quotas.provider_quota_bp, plugins.plugin_bp, iam.cloudru_iam_bp,
        environments.environment_bp, environments.environment_gateway_bp,
    ):
        app.register_blueprint(blueprint)
    yield app.test_client()
    close_memory_clients()


def test_remote_admin_reads_ignore_legacy_tokens(client, monkeypatch):
    monkeypatch.setattr(credentials, "_status_for", lambda provider: {"provider": provider})
    monkeypatch.setattr(quotas, "get_usage", lambda user_id: {"user_id": user_id})
    monkeypatch.setattr(plugins.plugin_manager, "discover", lambda: None)
    monkeypatch.setattr(plugins.plugin_manager, "list", lambda: [])
    headers = {"X-Forwarded-For": "192.0.2.50", "Authorization": "Bearer irrelevant"}
    for url in (
        "/api/provider-credentials/status", "/api/provider-quota/users/legacy-user", "/api/plugins",
    ):
        response = client.get(url, headers=headers, environ_base={"REMOTE_ADDR": "192.0.2.50"})
        assert response.status_code == 200, (url, response.get_json())


def test_quota_and_plugin_writes_without_admin_tokens(client, monkeypatch):
    updated = []
    monkeypatch.setattr(quotas, "upsert_policy", lambda *args, **kwargs: updated.append((args, kwargs)))
    monkeypatch.setattr(plugins.plugin_manager, "enable", lambda plugin_id: {"id": plugin_id, "enabled": True})
    response = client.put("/api/provider-quota/policies/demo", json={"max_requests": 5})
    assert response.status_code == 200
    assert updated[0][1]["max_requests"] == 5
    assert client.post("/api/plugins/demo/enable").get_json()["enabled"] is True


def test_provider_import_does_not_reject_origin(client, monkeypatch):
    import provider_accounts.issued_credentials as issued
    monkeypatch.setattr(issued, "import_yandex_key", lambda payload: {"accepted": payload["key"]})
    monkeypatch.setattr(credentials, "invalidate_model_discovery_cache", lambda: None)
    response = client.post("/api/provider-credentials/yandex/issued", json={"key": "fixture"}, headers={"Origin": "https://other.example"})
    assert response.status_code == 200
    assert response.get_json() == {"accepted": "fixture"}


def test_cloud_iam_keeps_feature_switch_without_token_gate(client, monkeypatch):
    monkeypatch.setenv("CLOUDRU_IAM_WIZARD_ENABLED", "true")
    monkeypatch.setattr(iam.CloudRuIamClient, "list_api_keys", lambda self, **kwargs: [])
    response = client.get("/api/cloudru/iam/api-keys?service_account_id=fixture")
    assert response.status_code == 200
    monkeypatch.setenv("CLOUDRU_IAM_WIZARD_ENABLED", "false")
    assert client.get("/api/cloudru/iam/api-keys?service_account_id=fixture").status_code == 404


def test_agent_register_poll_heartbeat_and_result_without_tokens(client):
    response = client.post("/api/local-agents/register", json={"agent_id": "phone-fixture"})
    assert response.status_code == 201
    assert "token" not in response.get_json()
    job_id = gateway.enqueue_local_tool_job("phone-fixture", "fixture.echo", {"value": 7})
    assert client.post("/api/local-agents/phone-fixture/heartbeat").status_code == 200
    job = client.get("/api/local-agents/phone-fixture/poll").get_json()["job"]
    assert job["id"] == job_id
    response = client.post(f"/api/local-agents/phone-fixture/jobs/{job_id}/result", json={"status": "completed", "result": {"value": 7}, "claim_token": job["claim_token"]})
    assert response.status_code == 200
    assert client.get("/api/local-agents/phone-fixture/poll").get_json()["job"] is None
    assert client.get("/api/local-agents/unknown/poll").status_code == 404
    assert client.get("/api/local-agents/health").status_code == 200


def test_agent_client_registers_without_bootstrap_token(client, monkeypatch):
    def request(method, url, **kwargs):
        assert not kwargs.get("token")
        response = client.open(url.replace("http://gateway", ""), method=method, json=kwargs.get("payload"))
        assert response.status_code < 400
        return response.get_json()
    monkeypatch.setattr(agent_client, "_request", request)
    descriptor = json.loads(agent_client.register_agent("http://gateway", agent_id="client-fixture"))
    assert descriptor["agent"]["id"] == "client-fixture"
    worker, legacy_token = agent_client.start_agent("http://gateway", agent_id="worker-fixture")
    assert worker.agent_id == "worker-fixture"
    assert legacy_token == ""


def test_local_job_result_requires_current_nonempty_claim(client):
    client.post("/api/local-agents/register", json={"agent_id": "claim-fixture"})
    job_id = gateway.enqueue_local_tool_job("claim-fixture", "fixture.echo", {})
    job = client.get("/api/local-agents/claim-fixture/poll").get_json()["job"]
    url = f"/api/local-agents/claim-fixture/jobs/{job_id}/result"
    payload = {"status": "completed", "result": {"success": True}}
    assert client.post(url, json=payload).status_code == 409
    assert client.post(url, json={**payload, "claim_token": "stale-claim"}).status_code == 409
    assert gateway.get_store().get("local-job/" + job_id)["status"] == "running"
    assert client.post(url, json={**payload, "claim_token": job["claim_token"]}).status_code == 200
    assert client.post(url, json={**payload, "claim_token": job["claim_token"]}).get_json()["idempotent"] is True


def test_legacy_running_job_cannot_complete_without_a_claim(client):
    client.post("/api/local-agents/register", json={"agent_id": "legacy-claim-fixture"})
    job_id = gateway.enqueue_local_tool_job("legacy-claim-fixture", "fixture.echo", {})
    key = "local-job/" + job_id
    with gateway.get_store().transaction() as tx:
        tx.set(key, {**tx.get(key), "status": "running"})
    response = client.post(
        f"/api/local-agents/legacy-claim-fixture/jobs/{job_id}/result",
        json={"status": "completed", "result": {"success": True}},
    )
    assert response.status_code == 409
    assert gateway.get_store().get(key)["status"] == "running"


def test_environment_routes_share_preexisting_records_without_owner_filter(client, monkeypatch):
    item = {"environment_id": "old", "owner_id": "old-user", "status": "STOPPED"}
    monkeypatch.setattr(environments, "list_environments", lambda: [item])
    monkeypatch.setattr(environments, "_get", lambda environment_id: item if environment_id == "old" else None)
    monkeypatch.setattr(environments, "stop_environment", lambda environment_id: {**item, "status": "STOPPED"})
    assert client.get("/api/environments").get_json()["environments"] == [item]
    assert client.get("/api/environments/old").status_code == 200
    assert client.post("/api/environments/old/stop").status_code == 200
    assert client.get("/environments/old/").status_code == 503
    assert client.get("/environments/missing/").status_code == 404


def test_agent_http_client_round_trip_without_login(tmp_path, monkeypatch):
    """Exercise urllib, HTTP routing, storage and the result protocol over a real socket."""
    import threading
    from werkzeug.serving import make_server

    monkeypatch.setenv("LOGOS_MEMORY_PATH", str(tmp_path / "http.memory"))
    app = Flask("local-agent-http-test")
    app.register_blueprint(gateway.local_agent_bp)
    server = make_server("127.0.0.1", 0, app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        descriptor = json.loads(agent_client.register_agent(base, agent_id="socket-fixture"))
        assert descriptor["agent"]["id"] == "socket-fixture"
        job_id = gateway.enqueue_local_tool_job("socket-fixture", "fixture.echo", {"value": 11})
        job = agent_client._request("GET", base + "/api/local-agents/socket-fixture/poll")["job"]
        assert job["id"] == job_id
        worker = agent_client.LocalToolAgent(base, "socket-fixture")
        worker._submit(job_id, "completed", {"success": True, "data": {"value": 11}}, job["claim_token"])
        assert agent_client._request("GET", base + "/api/local-agents/socket-fixture/poll")["job"] is None
        record = gateway.get_store().get("local-job/" + job_id)
        assert record["status"] == "completed"
        assert json.loads(record["result_json"])["data"] == {"value": 11}
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()
        close_memory_clients()


def test_remote_tool_executor_uses_real_local_job_result(client, monkeypatch):
    from tool_providers.universal_tool_platform import UniversalToolCall, UniversalToolDefinition, UniversalToolExecutor
    client.post("/api/local-agents/register", json={"agent_id": "executor-fixture"})
    enqueue = gateway.enqueue_local_tool_job
    def complete_immediately(*args, **kwargs):
        job_id = enqueue(*args, **kwargs)
        job = client.get("/api/local-agents/executor-fixture/poll").get_json()["job"]
        assert job["id"] == job_id
        response = client.post(f"/api/local-agents/executor-fixture/jobs/{job_id}/result", json={"status": "completed", "result": {"success": True, "data": {"value": 17}}, "claim_token": job["claim_token"]})
        assert response.status_code == 200
        return job_id
    monkeypatch.setattr(gateway, "enqueue_local_tool_job", complete_immediately)
    class Registry:
        def get_universal_definition(self, name):
            return UniversalToolDefinition(name, "fixture", {"type": "object"}, {"type": "object"}, executor={"type": "local_agent", "agent_id": "executor-fixture"})
    result = UniversalToolExecutor(Registry()).execute(UniversalToolCall("fixture.echo", {}))
    assert result["success"] is True
    assert result["data"] == {"value": 17}
    assert result["metadata"]["executor"] == "local_agent"


def test_local_job_wait_timeout_and_failure_are_bounded(client):
    import time
    client.post("/api/local-agents/register", json={"agent_id": "wait-fixture"})
    job_id = gateway.enqueue_local_tool_job("wait-fixture", "fixture.echo", {})
    started = time.monotonic()
    assert gateway.wait_for_local_tool_job(job_id, timeout_seconds=0) is None
    assert time.monotonic() - started < 0.1
    job = client.get("/api/local-agents/wait-fixture/poll").get_json()["job"]
    client.post(f"/api/local-agents/wait-fixture/jobs/{job_id}/result", json={"status": "failed", "error": {"reason": "fixture failure"}, "claim_token": job["claim_token"]})
    assert gateway.wait_for_local_tool_job(job_id, timeout_seconds=0) == {"success": False, "data": None, "error": {"reason": "fixture failure"}}

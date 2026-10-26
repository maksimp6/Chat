import time

import pytest

from agent_runtime import gateway, issue_runner, local_tool_agent


def test_result_is_resent_with_its_claim_token_and_never_run_again(monkeypatch):
    sent = []

    def flaky_request(method, url, payload=None, timeout=None):
        sent.append(payload)
        if len(sent) <= local_tool_agent._RESULT_ATTEMPTS:
            raise ConnectionError("response lost")
        return {"status": "ok"}

    monkeypatch.setattr(local_tool_agent, "_request", flaky_request)
    monkeypatch.setattr(local_tool_agent.time, "sleep", lambda seconds: None)
    agent = local_tool_agent.LocalToolAgent("http://gateway", "agent-1")

    agent._submit("job-1", "completed", {"success": True}, "claim-1")
    assert "job-1" in agent._undelivered
    agent._redeliver_pending()

    assert agent._undelivered == {}
    assert {payload["claim_token"] for payload in sent} == {"claim-1"}


def test_stale_claim_result_is_dropped_not_retried(monkeypatch):
    attempts = []

    def refused(method, url, payload=None, timeout=None):
        attempts.append(1)
        raise local_tool_agent.GatewayHTTPError(409, "claim_mismatch")

    monkeypatch.setattr(local_tool_agent, "_request", refused)
    agent = local_tool_agent.LocalToolAgent("http://gateway", "agent-1")

    agent._submit("job-2", "completed", {"success": True}, "old-claim")

    assert len(attempts) == 1
    assert agent._undelivered == {}


def test_timeout_is_its_own_error_so_a_running_handler_is_not_retried():
    def slow(payload):
        time.sleep(0.5)

    with pytest.raises(gateway.AgentTimeoutError):
        gateway.AgentGateway._invoke_once(slow, {}, 0.01)


def test_edit_to_an_already_dirty_file_is_detected(monkeypatch, tmp_path):
    monkeypatch.setattr(issue_runner, "changed_files", lambda repo_root=None: ["a.py"])
    (tmp_path / "a.py").write_text("one")
    before = issue_runner.change_snapshot(tmp_path)

    (tmp_path / "a.py").write_text("two")

    assert issue_runner.change_snapshot(tmp_path) != before

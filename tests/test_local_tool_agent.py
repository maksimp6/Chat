import local_tool_agent


def test_execute_job_normalizes_success(monkeypatch):
    captured = {}

    monkeypatch.setattr(
        local_tool_agent.registry,
        "execute",
        lambda name, args: {"value": args["value"]},
    )

    agent = local_tool_agent.LocalToolAgent(
        "https://example.invalid",
        "android-test",
        "token",
    )
    monkeypatch.setattr(
        agent,
        "_submit",
        lambda job_id, status, result: captured.update(
            job_id=job_id,
            status=status,
            result=result,
        ),
    )

    agent._execute_job(
        {
            "id": "job-1",
            "tool_name": "demo.echo",
            "arguments": {"value": 1},
        }
    )

    assert captured["job_id"] == "job-1"
    assert captured["status"] == "completed"
    assert captured["result"]["success"] is True
    assert captured["result"]["data"] == {"value": 1}
    assert captured["result"]["error"] is None
    assert captured["result"]["metadata"]["agent_id"] == "android-test"


def test_execute_job_turns_tool_errors_into_failed_result(monkeypatch):
    captured = {}

    monkeypatch.setattr(
        local_tool_agent.registry,
        "execute",
        lambda _name, _args: {"error": "permission denied"},
    )

    agent = local_tool_agent.LocalToolAgent(
        "https://example.invalid",
        "android-test",
        "token",
    )
    monkeypatch.setattr(
        agent,
        "_submit",
        lambda job_id, status, result: captured.update(
            job_id=job_id,
            status=status,
            result=result,
        ),
    )

    agent._execute_job(
        {
            "id": "job-2",
            "tool_name": "demo.write",
            "arguments": {},
        }
    )

    assert captured["status"] == "failed"
    assert captured["result"]["success"] is False
    assert captured["result"]["error"] == "permission denied"


def test_execute_job_rejects_invalid_payload(monkeypatch):
    captured = {}

    agent = local_tool_agent.LocalToolAgent(
        "https://example.invalid",
        "android-test",
        "token",
    )
    monkeypatch.setattr(
        agent,
        "_submit",
        lambda job_id, status, result: captured.update(
            job_id=job_id,
            status=status,
            result=result,
        ),
    )

    agent._execute_job({"id": "job-3", "tool_name": "demo.echo", "arguments": None})

    assert captured["job_id"] == "job-3"
    assert captured["status"] == "failed"
    assert captured["result"] == {"error": "invalid_job"}

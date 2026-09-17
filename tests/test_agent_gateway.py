import pytest

from agent_gateway import (
    AgentAlreadyRegistered,
    AgentDescriptor,
    AgentGateway,
    AgentNotFound,
)


def test_register_list_and_invoke_agent():
    gateway = AgentGateway()
    gateway.register(
        AgentDescriptor(
            agent_id="echo",
            name="Echo agent",
            capabilities=("chat",),
        ),
        lambda payload: {"echo": payload["message"]},
    )

    assert [agent.agent_id for agent in gateway.list_agents("chat")] == ["echo"]
    result = gateway.invoke("echo", {"message": "hello"})
    assert result.status == "completed"
    assert result.result == {"echo": "hello"}
    assert result.invocation_id


def test_duplicate_and_missing_agents():
    gateway = AgentGateway()
    descriptor = AgentDescriptor(agent_id="echo", name="Echo")
    gateway.register(descriptor, lambda payload: payload)
    with pytest.raises(AgentAlreadyRegistered):
        gateway.register(descriptor, lambda payload: payload)
    with pytest.raises(AgentNotFound):
        gateway.get("missing")


def test_handler_errors_are_returned_in_envelope():
    gateway = AgentGateway()
    gateway.register(
        AgentDescriptor(agent_id="broken", name="Broken"),
        lambda payload: 1 / 0,
    )
    result = gateway.invoke("broken", {})
    assert result.status == "error"
    assert "ZeroDivisionError" in (result.error or "")

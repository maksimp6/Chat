import pytest

from invocation_context import InvocationContext
from invocation_trace import create_invocation_trace
from runtime import (
    ConversationAgent,
    ConversationAgentRouter,
    RuntimeDispatcher,
    RuntimeScopeViolation,
)


def context(runtime_id: str, agent_id: str = "agent-a") -> InvocationContext:
    return InvocationContext(
        "session-1",
        "conversation-1",
        f"invocation-{runtime_id}",
        f"trace-{runtime_id}",
        agent_id=agent_id,
        runtime_id=runtime_id,
    )


def configured_router():
    calls = []
    dispatcher = RuntimeDispatcher()
    dispatcher.register_runtime("runtime-a")
    dispatcher.register_runtime("runtime-b")

    def invoke(runtime, payload):
        calls.append((runtime.runtime_id, payload))
        return {"runtime_id": runtime.runtime_id, "tool": payload["tool_name"]}

    dispatcher.register_operation(ConversationAgentRouter.TOOL_OPERATION, invoke)
    router = ConversationAgentRouter(dispatcher)
    for runtime_id in ("runtime-a", "runtime-b"):
        router.register(
            ConversationAgent(
                runtime_id, "conversation-1", "agent-a", config={"nested": {"value": runtime_id}}
            )
        )
        router.register(ConversationAgent(runtime_id, "conversation-1", "agent-b"))
    return router, calls


def test_two_agents_handoff_and_tool_call_are_correlated_and_traced():
    router, calls = configured_router()
    invocation = context("runtime-a")
    trace = create_invocation_trace(invocation)

    target = router.handoff(invocation, "agent-a", "agent-b", trace)
    result = router.invoke_tool(
        invocation, target, "documents.read", {"secret": "not-traced"}, trace
    )

    assert result == {"runtime_id": "runtime-a", "tool": "documents.read"}
    assert calls[0][1]["invocation_id"] == invocation.invocation_id
    assert calls[0][1]["trace_id"] == invocation.trace_id
    events = trace.trace["events"]
    assert [event["type"] for event in events].count("conversation_agent_selected") == 2
    assert "conversation_agent_handoff" in [event["type"] for event in events]
    tool_event = next(
        event for event in events if event["type"] == "conversation_agent_tool_invoked"
    )
    assert tool_event["payload"]["agent_id"] == "agent-b"
    assert "arguments" not in tool_event["payload"]
    assert "secret" not in str(tool_event)


def test_two_runtimes_do_not_share_agent_configuration():
    router, _ = configured_router()
    trace_a = create_invocation_trace(context("runtime-a"))
    trace_b = create_invocation_trace(context("runtime-b"))

    agent_a = router.select(context("runtime-a"), "agent-a", trace_a)
    agent_b = router.select(context("runtime-b"), "agent-a", trace_b)

    assert agent_a.config["nested"]["value"] == "runtime-a"
    assert agent_b.config["nested"]["value"] == "runtime-b"
    with pytest.raises(TypeError):
        agent_a.config["nested"]["value"] = "leak"


def test_cross_runtime_agent_invocation_is_rejected_before_dispatch():
    router, calls = configured_router()
    invocation_a = context("runtime-a")
    trace_a = create_invocation_trace(invocation_a)
    agent_b = router.select(
        context("runtime-b"), "agent-a", create_invocation_trace(context("runtime-b"))
    )

    with pytest.raises(RuntimeScopeViolation):
        router.invoke_tool(invocation_a, agent_b, "documents.read", {}, trace_a)

    assert calls == []


def test_invocation_context_legacy_shape_and_agent_trace_context():
    legacy = InvocationContext("session", "conversation", "invocation", "trace")
    assert "agent_id" not in legacy.as_dict()
    assert "runtime_id" not in legacy.as_dict()

    routed = context("runtime-a", "agent-b")
    trace = create_invocation_trace(routed)
    assert trace.trace["context"]["agent_id"] == "agent-b"
    assert trace.trace["context"]["runtime_id"] == "runtime-a"

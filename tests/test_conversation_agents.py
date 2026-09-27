import pytest

from conversation_agents import AgentSelection, ConversationAgent, ConversationAgentRouter
from invocation_context import InvocationContext
from invocation_trace import create_invocation_trace
from runtime import RuntimeDispatcher, RuntimeScopeViolation


def _context(runtime_id, conversation_id, agent_id):
    return InvocationContext(
        "session", conversation_id, f"inv-{runtime_id}-{agent_id}", f"trace-{runtime_id}-{agent_id}",
        agent_id=agent_id, runtime_id=runtime_id,
    )


def test_two_agents_handoff_and_tools_are_traced_through_dispatcher():
    dispatcher = RuntimeDispatcher()
    dispatcher.register_runtime("runtime-a")
    calls = []

    def invoke(runtime, payload):
        calls.append((runtime.runtime_id, payload))
        return {"ok": True}

    dispatcher.register_operation(ConversationAgentRouter.TOOL_OPERATION, invoke)
    router = ConversationAgentRouter(dispatcher)
    first = ConversationAgent("agent-a", "conversation-a", "runtime-a", {"model": "one"})
    second = ConversationAgent("agent-b", "conversation-a", "runtime-a", {"model": "two"})
    router.register(first)
    router.register(second)

    context = _context("runtime-a", "conversation-a", "agent-a")
    trace = create_invocation_trace(context)
    selected = router.select(AgentSelection("agent-a", "conversation-a", "runtime-a"), context, trace)
    assert router.invoke_tool(selected, context, trace, {"name": "safe-tool", "arguments": {}}) == {"ok": True}

    handoff_context = _context("runtime-a", "conversation-a", "agent-b")
    target = router.handoff(
        selected, AgentSelection("agent-b", "conversation-a", "runtime-a"),
        handoff_context, trace,
    )
    assert target is second
    assert calls[0][0] == "runtime-a"
    assert calls[0][1]["invocation_context"]["trace_id"] == context.trace_id
    event_types = [event["type"] for event in trace.trace["events"]]
    assert "conversation_agent_handoff" in event_types
    assert "conversation_agent_tool_invocation" in event_types


def test_two_runtimes_cannot_leak_agent_config_or_route_state():
    dispatcher = RuntimeDispatcher()
    dispatcher.register_runtime("runtime-a")
    dispatcher.register_runtime("runtime-b")
    router = ConversationAgentRouter(dispatcher)
    config_a = {"memory": {"owner": "a"}}
    agent_a = ConversationAgent("agent", "conversation", "runtime-a", config_a)
    agent_b = ConversationAgent("agent", "conversation", "runtime-b", {"memory": {"owner": "b"}})
    router.register(agent_a)
    router.register(agent_b)
    config_a["memory"]["owner"] = "changed"

    context_a = _context("runtime-a", "conversation", "agent")
    context_b = _context("runtime-b", "conversation", "agent")
    selected_a = router.select(AgentSelection("agent", "conversation", "runtime-a"), context_a, create_invocation_trace(context_a))
    selected_b = router.select(AgentSelection("agent", "conversation", "runtime-b"), context_b, create_invocation_trace(context_b))
    assert selected_a.config["memory"]["owner"] == "a"
    assert selected_b.config["memory"]["owner"] == "b"

    with pytest.raises(RuntimeScopeViolation):
        router.select(
            AgentSelection("agent", "conversation", "runtime-b"),
            context_a,
            create_invocation_trace(context_a),
        )

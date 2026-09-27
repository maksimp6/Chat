"""Request-scoped routing foundation for conversation agents.

This module deliberately does not own tool registries or runtime resources.
Callers provide a :class:`RuntimeDispatcher`, which remains the only route to
the policy-controlled ``agent.tool.invoke`` operation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import RLock
from types import MappingProxyType
from typing import Any, Mapping

from invocation_context import InvocationContext
from runtime import RuntimeDispatcher, RuntimeScopeViolation
from trace_manager import ExecutionTrace


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, set):
        return frozenset(_freeze(item) for item in value)
    return value


@dataclass(frozen=True)
class ConversationAgent:
    """Immutable agent identity and non-secret configuration for one scope."""

    agent_id: str
    conversation_id: str
    runtime_id: str
    config: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.agent_id or not self.conversation_id or not self.runtime_id:
            raise ValueError("agent_id, conversation_id, and runtime_id are required")
        object.__setattr__(self, "config", _freeze(self.config))


@dataclass(frozen=True)
class AgentSelection:
    """Explicit, caller-selected routing key; there is no global/default agent."""

    agent_id: str
    conversation_id: str
    runtime_id: str


class AgentRouteNotFound(LookupError):
    pass


class ConversationAgentRouter:
    """Instance-owned agent directory and observable routing contract."""

    TOOL_OPERATION = "agent.tool.invoke"

    def __init__(self, dispatcher: RuntimeDispatcher) -> None:
        self._dispatcher = dispatcher
        self._lock = RLock()
        self._agents: dict[tuple[str, str, str], ConversationAgent] = {}

    def register(self, agent: ConversationAgent) -> None:
        # Registration proves the runtime exists; no implicit runtime is created.
        self._dispatcher.context(agent.runtime_id)
        key = (agent.runtime_id, agent.conversation_id, agent.agent_id)
        with self._lock:
            self._agents[key] = agent

    def select(
        self,
        selection: AgentSelection,
        context: InvocationContext,
        trace: ExecutionTrace,
    ) -> ConversationAgent:
        self._validate_context(selection, context)
        key = (selection.runtime_id, selection.conversation_id, selection.agent_id)
        with self._lock:
            agent = self._agents.get(key)
        if agent is None:
            raise AgentRouteNotFound(key)
        trace.add_event(
            "conversation_agent_selected",
            {"agent_id": agent.agent_id, "runtime_id": agent.runtime_id},
        )
        return agent

    def handoff(
        self,
        source: ConversationAgent,
        selection: AgentSelection,
        context: InvocationContext,
        trace: ExecutionTrace,
    ) -> ConversationAgent:
        if source.runtime_id != selection.runtime_id:
            raise RuntimeScopeViolation("agent handoffs cannot cross runtime boundaries")
        if source.conversation_id != selection.conversation_id:
            raise ValueError("agent handoffs cannot cross conversation boundaries")
        target = self.select(selection, context, trace)
        trace.add_event(
            "conversation_agent_handoff",
            {
                "source_agent_id": source.agent_id,
                "target_agent_id": target.agent_id,
                "runtime_id": target.runtime_id,
            },
        )
        return target

    def invoke_tool(
        self,
        agent: ConversationAgent,
        context: InvocationContext,
        trace: ExecutionTrace,
        tool_call: Mapping[str, Any],
    ) -> Any:
        self._validate_context(
            AgentSelection(agent.agent_id, agent.conversation_id, agent.runtime_id), context
        )
        tool_name = str(tool_call.get("tool_name") or tool_call.get("name") or "")
        trace.add_event(
            "conversation_agent_tool_invocation",
            {"agent_id": agent.agent_id, "runtime_id": agent.runtime_id, "tool": tool_name},
        )
        return self._dispatcher.dispatch(
            agent.runtime_id,
            self.TOOL_OPERATION,
            {
                "agent_id": agent.agent_id,
                "conversation_id": agent.conversation_id,
                "invocation_context": context.as_dict(),
                "tool_call": dict(tool_call),
            },
            resource_runtime_id=agent.runtime_id,
        )

    @staticmethod
    def _validate_context(selection: AgentSelection, context: InvocationContext) -> None:
        if context.runtime_id != selection.runtime_id:
            raise RuntimeScopeViolation("invocation and agent runtime ids do not match")
        if context.conversation_id != selection.conversation_id:
            raise ValueError("invocation and agent conversation ids do not match")
        if context.agent_id and context.agent_id != selection.agent_id:
            raise ValueError("invocation and selected agent ids do not match")

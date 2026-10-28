"""Conversation-scoped agent selection and dispatcher-only tool routing."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

from invocation.context import InvocationContext
from observability.trace_manager import ExecutionTrace

from runtime.dispatcher import RuntimeDispatcher


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, set | frozenset):
        return frozenset(_freeze(item) for item in value)
    return value


@dataclass(frozen=True)
class ConversationAgent:
    """Immutable agent configuration owned by one runtime and conversation."""

    runtime_id: str
    conversation_id: str
    agent_id: str
    definition: str = "general"
    config: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("runtime_id", "conversation_id", "agent_id", "definition"):
            if not str(getattr(self, name) or "").strip():
                raise ValueError(f"{name} is required")
        object.__setattr__(self, "config", _freeze(self.config))


class ConversationAgentRouter:
    """Route explicit agent selections without process-global agent state."""

    TOOL_OPERATION = "agent.tool.invoke"

    def __init__(self, dispatcher: RuntimeDispatcher) -> None:
        self._dispatcher = dispatcher
        self._agents: dict[tuple[str, str, str], ConversationAgent] = {}

    def register(self, agent: ConversationAgent) -> None:
        key = (agent.runtime_id, agent.conversation_id, agent.agent_id)
        self._agents[key] = agent

    def select(
        self, context: InvocationContext, agent_id: str, trace: ExecutionTrace
    ) -> ConversationAgent:
        self._validate_context(context)
        key = (context.runtime_id or "", context.conversation_id, str(agent_id))
        try:
            agent = self._agents[key]
        except KeyError as exc:
            raise KeyError("agent is not registered in this runtime and conversation") from exc
        trace.add_event(
            "conversation_agent_selected",
            {
                "runtime_id": agent.runtime_id,
                "conversation_id": agent.conversation_id,
                "agent_id": agent.agent_id,
            },
        )
        return agent

    def handoff(
        self,
        context: InvocationContext,
        source_agent_id: str,
        target_agent_id: str,
        trace: ExecutionTrace,
    ) -> ConversationAgent:
        source = self.select(context, source_agent_id, trace)
        target = self.select(context, target_agent_id, trace)
        trace.add_event(
            "conversation_agent_handoff",
            {
                "runtime_id": target.runtime_id,
                "conversation_id": target.conversation_id,
                "source_agent_id": source.agent_id,
                "target_agent_id": target.agent_id,
            },
        )
        return target

    @staticmethod
    def _validate_context(context: InvocationContext) -> None:
        if not context.runtime_id:
            raise ValueError("runtime_id is required for conversation-agent routing")

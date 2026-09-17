"""Provider-agnostic gateway for registering and invoking Alice Pro agents.

The gateway intentionally contains no provider SDK dependencies. Adapters can
implement ``AgentHandler`` for local agents, MCP, REST, A2A, or other systems.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Callable, Dict, Iterable, Mapping, MutableMapping, Optional
from uuid import uuid4


class AgentGatewayError(Exception):
    """Base exception for gateway errors."""


class AgentAlreadyRegistered(AgentGatewayError):
    pass


class AgentNotFound(AgentGatewayError):
    pass


class AgentInvocationError(AgentGatewayError):
    pass


AgentHandler = Callable[[Mapping[str, Any]], Any]


@dataclass(frozen=True)
class AgentDescriptor:
    """Public metadata used to discover and route work to an agent."""

    agent_id: str
    name: str
    capabilities: tuple[str, ...] = ()
    version: str = "1.0"
    transport: str = "local"
    enabled: bool = True
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class InvocationResult:
    """Stable result envelope returned by the gateway."""

    invocation_id: str
    agent_id: str
    status: str
    result: Any = None
    error: Optional[str] = None
    started_at: str = ""
    finished_at: str = ""


class AgentGateway:
    """Thread-safe registry and dispatcher for Alice Pro agents."""

    def __init__(self) -> None:
        self._agents: MutableMapping[str, tuple[AgentDescriptor, AgentHandler]] = {}
        self._lock = RLock()

    def register(self, descriptor: AgentDescriptor, handler: AgentHandler) -> None:
        if not descriptor.agent_id.strip():
            raise ValueError("agent_id must not be empty")
        if not callable(handler):
            raise TypeError("handler must be callable")
        with self._lock:
            if descriptor.agent_id in self._agents:
                raise AgentAlreadyRegistered(descriptor.agent_id)
            self._agents[descriptor.agent_id] = (descriptor, handler)

    def unregister(self, agent_id: str) -> None:
        with self._lock:
            if agent_id not in self._agents:
                raise AgentNotFound(agent_id)
            del self._agents[agent_id]

    def get(self, agent_id: str) -> AgentDescriptor:
        with self._lock:
            entry = self._agents.get(agent_id)
            if entry is None:
                raise AgentNotFound(agent_id)
            return entry[0]

    def list_agents(self, capability: Optional[str] = None) -> list[AgentDescriptor]:
        with self._lock:
            descriptors = [descriptor for descriptor, _ in self._agents.values()]
        if capability is not None:
            descriptors = [
                descriptor
                for descriptor in descriptors
                if capability in descriptor.capabilities
            ]
        return sorted(descriptors, key=lambda item: item.agent_id)

    def invoke(self, agent_id: str, payload: Mapping[str, Any]) -> InvocationResult:
        invocation_id = str(uuid4())
        started_at = datetime.now(timezone.utc).isoformat()
        with self._lock:
            entry = self._agents.get(agent_id)
        if entry is None:
            raise AgentNotFound(agent_id)
        descriptor, handler = entry
        if not descriptor.enabled:
            raise AgentInvocationError(f"agent is disabled: {agent_id}")
        try:
            result = handler(payload)
        except Exception as exc:  # preserve a stable envelope for callers
            finished_at = datetime.now(timezone.utc).isoformat()
            return InvocationResult(
                invocation_id=invocation_id,
                agent_id=agent_id,
                status="error",
                error=f"{type(exc).__name__}: {exc}",
                started_at=started_at,
                finished_at=finished_at,
            )
        return InvocationResult(
            invocation_id=invocation_id,
            agent_id=agent_id,
            status="completed",
            result=result,
            started_at=started_at,
            finished_at=datetime.now(timezone.utc).isoformat(),
        )


__all__ = [
    "AgentAlreadyRegistered",
    "AgentDescriptor",
    "AgentGateway",
    "AgentGatewayError",
    "AgentInvocationError",
    "AgentNotFound",
    "InvocationResult",
]

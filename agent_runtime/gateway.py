"""Provider-agnostic gateway for Logos agents."""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass, field
from threading import RLock
from typing import Any


class AgentAlreadyRegistered(Exception):
    pass


@dataclass(frozen=True)
class AgentDescriptor:
    agent_id: str
    name: str
    capabilities: tuple[str, ...] = ()
    version: str = "1.0"
    transport: str = "local"
    enabled: bool = True
    metadata: Mapping[str, Any] = field(default_factory=dict)
    risk_level: str = "low"
    requires_approval: bool = False
    allowed_users: tuple[str, ...] = ()


class AgentGateway:
    def __init__(self) -> None:
        self._agents: MutableMapping[str, AgentDescriptor] = {}
        self._lock = RLock()

    def register(self, descriptor: AgentDescriptor) -> None:
        if not descriptor.agent_id.strip():
            raise ValueError("agent_id must not be empty")
        if descriptor.risk_level not in {"low", "medium", "high", "critical"}:
            raise ValueError("unsupported risk_level")
        with self._lock:
            if descriptor.agent_id in self._agents:
                raise AgentAlreadyRegistered(descriptor.agent_id)
            self._agents[descriptor.agent_id] = descriptor

    def list_agents(self, capability: str | None = None) -> list[AgentDescriptor]:
        with self._lock:
            items = list(self._agents.values())
        if capability is not None:
            items = [a for a in items if capability in a.capabilities]
        return sorted(items, key=lambda a: a.agent_id)


__all__ = ["AgentAlreadyRegistered", "AgentDescriptor", "AgentGateway"]

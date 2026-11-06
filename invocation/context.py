"""Immutable request-scoped execution context for Logos invocations."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any


@dataclass(frozen=True)
class InvocationContext:
    """Identifiers and metadata belonging to exactly one invocation.

    The context is intentionally request-scoped. It contains correlation data,
    not credentials or other secrets.
    """

    session_id: str
    conversation_id: str
    invocation_id: str
    trace_id: str
    user_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    agent_id: str | None = None
    runtime_id: str | None = None

    def __post_init__(self) -> None:
        if not self.session_id or not self.conversation_id:
            raise ValueError("session_id and conversation_id are required")
        if not self.invocation_id or not self.trace_id:
            raise ValueError("invocation_id and trace_id are required")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

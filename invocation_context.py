"""Immutable request-scoped execution context for Alice Pro invocations."""

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Dict, Mapping, Optional
import uuid


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
    user_id: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.session_id or not self.conversation_id:
            raise ValueError("session_id and conversation_id are required")
        if not self.invocation_id or not self.trace_id:
            raise ValueError("invocation_id and trace_id are required")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    @classmethod
    def create(
        cls,
        session_id: str,
        conversation_id: str,
        *,
        user_id: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> "InvocationContext":
        """Create a fresh invocation context with unique correlation IDs."""
        return cls(
            session_id=session_id,
            conversation_id=conversation_id,
            invocation_id=str(uuid.uuid4()),
            trace_id=str(uuid.uuid4()),
            user_id=user_id,
            metadata=metadata or {},
        )

    def child_metadata(self, **updates: Any) -> Dict[str, Any]:
        result = dict(self.metadata)
        result.update(updates)
        return result

    def as_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "conversation_id": self.conversation_id,
            "invocation_id": self.invocation_id,
            "trace_id": self.trace_id,
            "user_id": self.user_id,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "InvocationContext":
        required = ("session_id", "conversation_id", "invocation_id", "trace_id")
        missing = [key for key in required if not data.get(key)]
        if missing:
            raise ValueError(f"Missing invocation context fields: {', '.join(missing)}")
        return cls(
            session_id=str(data["session_id"]),
            conversation_id=str(data["conversation_id"]),
            invocation_id=str(data["invocation_id"]),
            trace_id=str(data["trace_id"]),
            user_id=str(data["user_id"]) if data.get("user_id") else None,
            metadata=dict(data.get("metadata") or {}),
        )

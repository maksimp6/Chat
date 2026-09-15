"""Immutable request-scoped execution context for Alice Pro invocations."""

from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass(frozen=True)
class InvocationContext:
    """Identifiers and metadata belonging to exactly one invocation."""

    session_id: str
    conversation_id: str
    invocation_id: str
    trace_id: str
    metadata: Dict[str, Any] = field(default_factory=dict)

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
            metadata=dict(data.get("metadata") or {}),
        )

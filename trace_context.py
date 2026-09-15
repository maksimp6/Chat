"""Request-scoped ExecutionTrace binding without passing trace through params."""

from contextvars import ContextVar
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from invocation_context import InvocationContext
    from trace_manager import ExecutionTrace

_current_context: ContextVar[Optional["InvocationContext"]] = ContextVar("alice_invocation_context", default=None)
_current_trace: ContextVar[Optional["ExecutionTrace"]] = ContextVar("alice_execution_trace", default=None)


def bind(context: "InvocationContext", trace: "ExecutionTrace") -> None:
    _current_context.set(context)
    _current_trace.set(trace)


def get_context() -> Optional["InvocationContext"]:
    return _current_context.get()


def get_trace() -> Optional["ExecutionTrace"]:
    return _current_trace.get()


def clear() -> None:
    _current_context.set(None)
    _current_trace.set(None)

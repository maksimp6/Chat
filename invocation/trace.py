"""Invocation-scoped ExecutionTrace ownership helpers."""

from invocation.context import InvocationContext
from observability.trace_manager import ExecutionTrace


def create_invocation_trace(context: InvocationContext) -> ExecutionTrace:
    """Create a trace bound to exactly one invocation context."""
    trace = ExecutionTrace(trace_id=context.trace_id)
    trace.set_context(
        invocation_id=context.invocation_id,
        session_id=context.session_id,
        conversation_id=context.conversation_id,
        user_id=context.user_id,
        agent_id=context.agent_id,
        runtime_id=context.runtime_id,
    )
    return trace

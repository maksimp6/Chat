"""Invocation-scoped execution gateway for declarative plugins."""

from __future__ import annotations

from typing import Any, Mapping

from invocation_context import InvocationContext
from invocation_trace import create_invocation_trace
from plugin_manager import PluginError, PluginManager
from runtime.dispatcher import RuntimeDispatcher
from universal_tool_platform import UniversalToolCall, UniversalToolExecutor


class PluginExecutionGateway:
    """Execute a plugin request through runtime and universal-tool boundaries."""

    OPERATION = "plugin.execute_tool"

    def __init__(self, manager: PluginManager, registry: Any):
        self.manager = manager
        self.executor = UniversalToolExecutor(registry)

    def execute(
        self,
        plugin_id: str,
        tool_name: str,
        arguments: Mapping[str, Any],
        context: InvocationContext,
        *,
        approved: bool = False,
    ) -> dict[str, Any]:
        record = self.manager.get(plugin_id)
        if record.state != "enabled":
            raise PluginError(f"plugin is not enabled: {plugin_id}")

        dispatcher = RuntimeDispatcher()
        runtime_id = f"plugin:{plugin_id}:{context.invocation_id}"
        dispatcher.register_runtime(runtime_id, owner_id=context.user_id)

        def execute_tool(_runtime_context, payload):
            call = UniversalToolCall(
                tool_name=payload["tool_name"],
                arguments=payload["arguments"],
                transport="internal",
                trace_id=context.trace_id,
                invocation_id=context.invocation_id,
                user_id=context.user_id,
                approved=approved,
                metadata={"plugin_id": plugin_id, "runtime_id": runtime_id},
            )

            def authorize(definition, _call):
                if f"tool:{definition.name}" not in record.manifest.permissions:
                    raise PermissionError(f"plugin permission denied for tool: {definition.name}")
                missing = set(definition.capabilities) - set(record.manifest.capabilities)
                if missing:
                    raise PermissionError(
                        "plugin capability denied: " + ", ".join(sorted(missing))
                    )
                return True

            trace = create_invocation_trace(context)
            result = self.executor.execute_with_trace(call, trace, authorize=authorize)
            result["metadata"].update(
                {"plugin_id": plugin_id, "runtime_id": runtime_id, "trace_id": context.trace_id,
                 "invocation_id": context.invocation_id}
            )
            return result

        dispatcher.register_operation(self.OPERATION, execute_tool)
        try:
            return dispatcher.dispatch(
                runtime_id,
                self.OPERATION,
                {"tool_name": tool_name, "arguments": dict(arguments)},
            )
        finally:
            dispatcher.unregister_runtime(runtime_id)

"""Capability-gated plugin access to Alice's existing execution boundaries."""

from __future__ import annotations

from typing import Any, Mapping

from invocation_context import InvocationContext
from plugin_manager import PluginError, PluginManager
from runtime import RuntimeDispatcher
from universal_tool_platform import UniversalToolCall, UniversalToolExecutor


class PluginExecutionGateway:
    """Execute declared plugin tool requests without exposing host services.

    A plugin supplies only a tool name and JSON-like arguments. The gateway
    binds a dedicated runtime scope, checks the immutable manifest grant, and
    delegates execution to ``UniversalToolExecutor`` for schema, policy,
    approval, and result handling.
    """

    OPERATION = "plugin.tool.execute"

    def __init__(self, manager: PluginManager, registry: Any, dispatcher: RuntimeDispatcher):
        self.manager = manager
        self.executor = UniversalToolExecutor(registry)
        self.dispatcher = dispatcher
        self.dispatcher.register_operation(self.OPERATION, self._execute_in_scope)

    def execute(
        self,
        plugin_id: str,
        tool_name: str,
        arguments: Mapping[str, Any],
        context: InvocationContext,
        *,
        trace: Any = None,
        approved: bool = False,
    ) -> dict[str, Any]:
        record = self.manager.get(plugin_id)
        if record.state != "enabled":
            raise PluginError(f"plugin is not enabled: {plugin_id}")
        if not isinstance(arguments, Mapping):
            raise PluginError("tool arguments must be an object")

        runtime_id = f"plugin:{plugin_id}:{context.invocation_id}"
        self.dispatcher.register_runtime(
            runtime_id,
            owner_id=context.user_id,
            namespace=f"plugin:{plugin_id}",
            root=str(record.path),
        )
        try:
            return self.dispatcher.dispatch(
                runtime_id,
                self.OPERATION,
                {
                    "plugin_id": plugin_id,
                    "tool_name": str(tool_name),
                    "arguments": dict(arguments),
                    "context": context,
                    "trace": trace,
                    "approved": approved,
                },
            )
        finally:
            self.dispatcher.unregister_runtime(runtime_id)

    def _execute_in_scope(self, runtime_context, payload: Mapping[str, Any]) -> dict[str, Any]:
        plugin_id = str(payload["plugin_id"])
        record = self.manager.get(plugin_id)
        tool_name = str(payload["tool_name"])
        required_permission = f"tool:{tool_name}"
        context = payload["context"]

        call = UniversalToolCall(
            tool_name=tool_name,
            arguments=dict(payload["arguments"]),
            transport="internal",
            trace_id=context.trace_id,
            invocation_id=context.invocation_id,
            user_id=context.user_id,
            approved=bool(payload["approved"]),
            metadata={
                "plugin_id": plugin_id,
                "plugin_runtime_id": runtime_context.runtime_id,
            },
        )

        def authorize(definition, _call):
            if required_permission not in record.manifest.permissions:
                raise PermissionError(f"plugin permission denied: {required_permission}")
            missing = set(definition.capabilities) - set(record.manifest.capabilities)
            if missing:
                raise PermissionError("plugin capability denied: " + ", ".join(sorted(missing)))
            return True

        result = self.executor.execute_with_trace(
            call,
            payload.get("trace"),
            authorize=authorize,
        )
        result.setdefault("metadata", {}).update(
            {
                "plugin_id": plugin_id,
                "plugin_runtime_id": runtime_context.runtime_id,
                "trace_id": context.trace_id,
                "invocation_id": context.invocation_id,
            }
        )
        return result

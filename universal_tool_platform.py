                error=f"{type(exc).__name__}: {exc}",
                metadata={
                    **base_meta,
                    "phase": "execution",
                    "executor": dict(definition.executor),
                    "duration_ms": round(max(0.0, self.clock() - started) * 1000, 2),
                },
            ).to_mapping()

    def execute_with_trace(self, call: UniversalToolCall, trace, **hooks) -> dict[str, Any]:
        if trace is None:
            return self.execute(call, **hooks)

        definition = self.registry.get_universal_definition(call.tool_name)
        trace_arguments = dict(call.arguments)
        if definition is not None:
            redact = set(
                dict(definition.metadata or {}).get("trace_redact_arguments") or ()
            )
            for key in redact:
                if key in trace_arguments:
                    trace_arguments[key] = "<redacted>"

        traced_call = replace(
            call,
            metadata={
                **dict(call.metadata),
                "execution_trace": trace,
            },
        )
        result = trace.track_tool_execution(
            call.tool_name,
            dict(call.arguments),
            lambda: self.execute(traced_call, **hooks),
            call_id=call.call_id,
            step=None,
            trace_arguments=trace_arguments,
        )
        redact_result_fields = set(
            dict(definition.metadata or {}).get("trace_redact_result_fields") or ()
        ) if definition is not None else set()
        def redact_fields(value):
            if isinstance(value, dict):
                return {
                    key: "<redacted>" if key in redact_result_fields
                    else redact_fields(item)
                    for key, item in value.items()
                }
            if isinstance(value, list):
                return [redact_fields(item) for item in value]
            return value

        if redact_result_fields:
            for entry in reversed(trace.trace.get("tool_calls", [])):
                if entry.get("call_id") == call.call_id:
                    entry["result"] = redact_fields(entry.get("result"))
                    break
        return result

    def _execute_remote(
        self,
        definition: UniversalToolDefinition,
        call: UniversalToolCall,
        target: Mapping[str, Any],
    ) -> Any:
        from local_agent_gateway import enqueue_local_tool_job, wait_for_local_tool_job

        agent_id = str(target.get("agent_id") or target.get("target_agent") or "").strip()
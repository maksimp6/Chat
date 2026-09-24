"""Provider-agnostic tool contracts and execution pipeline for Alice Pro.

The module is intentionally independent from Flask/MCP/Android presentation code.
Adapters create a UniversalToolCall and hand it to UniversalToolExecutor.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Callable, Mapping, Optional
import time


@dataclass(frozen=True)
class UniversalToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    capabilities: tuple[str, ...] = ()
    risk_level: str = "medium"
    read_only: bool = False
    requires_approval: bool = True
    supported_transports: tuple[str, ...] = ("responses_api", "local_agent")
    executor: Mapping[str, Any] = field(default_factory=lambda: {"type": "local"})
    title: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, name: str, cfg: Mapping[str, Any]) -> "UniversalToolDefinition":
        return cls(
            name=name,
            title=str(cfg.get("title") or name),
            description=str(cfg.get("description") or ""),
            input_schema=dict(
                cfg.get("inputSchema")
                or cfg.get("input_schema")
                or cfg.get("parameters")
                or {"type": "object", "properties": {}, "additionalProperties": False}
            ),
            output_schema=dict(
                cfg.get("outputSchema")
                or cfg.get("output_schema")
                or {"type": "object"}
            ),
            capabilities=tuple(str(x) for x in (cfg.get("capabilities") or ())),
            risk_level=str(cfg.get("risk_level") or "medium"),
            read_only=bool(cfg.get("read_only", False)),
            requires_approval=bool(cfg.get("requires_approval", True)),
            supported_transports=tuple(
                str(x) for x in (
                    cfg.get("supported_transports")
                    or ("responses_api", "local_agent")
                )
            ),
            executor=dict(cfg.get("executor") or {"type": "local"}),
            metadata=dict(cfg.get("metadata") or {}),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title or self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
            "outputSchema": self.output_schema,
            "capabilities": list(self.capabilities),
            "risk_level": self.risk_level,
            "read_only": self.read_only,
            "requires_approval": self.requires_approval,
            "supported_transports": list(self.supported_transports),
            "executor": dict(self.executor),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class UniversalToolCall:
    tool_name: str
    arguments: Mapping[str, Any]
    call_id: Optional[str] = None
    transport: str = "internal"
    trace_id: Optional[str] = None
    invocation_id: Optional[str] = None
    user_id: Optional[str] = None
    approved: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class UniversalToolResult:
    success: bool
    data: Any = None
    error: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_mapping(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "data": self.data if self.success else None,
            "error": self.error,
            "metadata": dict(self.metadata),
        }


def _type_matches(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, Mapping)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return True


def validate_json_schema(value: Any, schema: Mapping[str, Any], path: str = "$") -> list[str]:
    """Validate the JSON-schema features used by the tool contracts.

    This is deliberately dependency-free so the same contract layer can be
    imported by the Android/Chaquopy agent. Unsupported schema keywords are
    ignored rather than treated as implicit validation failures.
    """
    if not isinstance(schema, Mapping):
        return []

    errors: list[str] = []

    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        errors.append(f"{path}: value is not one of enum choices")
        return errors

    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: value does not match const")
        return errors

    any_of = schema.get("anyOf")
    if isinstance(any_of, list):
        if any(
            not validate_json_schema(value, option, path)
            for option in any_of
            if isinstance(option, Mapping)
        ):
            return []
        errors.append(f"{path}: value does not match anyOf")
        return errors

    expected_type = schema.get("type")
    if isinstance(expected_type, list):
        if not any(_type_matches(value, str(t)) for t in expected_type):
            errors.append(f"{path}: expected one of types {expected_type}")
            return errors
    elif isinstance(expected_type, str) and not _type_matches(value, expected_type):
        errors.append(f"{path}: expected type {expected_type}")
        return errors

    if isinstance(value, str):
        min_length = schema.get("minLength")
        max_length = schema.get("maxLength")
        if isinstance(min_length, int) and len(value) < min_length:
            errors.append(f"{path}: length must be >= {min_length}")
        if isinstance(max_length, int) and len(value) > max_length:
            errors.append(f"{path}: length must be <= {max_length}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if isinstance(minimum, (int, float)) and value < minimum:
            errors.append(f"{path}: value must be >= {minimum}")
        if isinstance(maximum, (int, float)) and value > maximum:
            errors.append(f"{path}: value must be <= {maximum}")

    if isinstance(value, list):
        min_items = schema.get("minItems")
        max_items = schema.get("maxItems")
        if isinstance(min_items, int) and len(value) < min_items:
            errors.append(f"{path}: item count must be >= {min_items}")
        if isinstance(max_items, int) and len(value) > max_items:
            errors.append(f"{path}: item count must be <= {max_items}")
        item_schema = schema.get("items")
        if isinstance(item_schema, Mapping):
            for index, item in enumerate(value):
                errors.extend(validate_json_schema(item, item_schema, f"{path}[{index}]"))

    if isinstance(value, Mapping):
        properties = schema.get("properties")
        if isinstance(properties, Mapping):
            required = schema.get("required") or []
            for name in required:
                if name not in value:
                    errors.append(f"{path}.{name}: required property is missing")

            additional = schema.get("additionalProperties", True)
            if additional is False:
                unknown = [key for key in value if key not in properties]
                for key in unknown:
                    errors.append(f"{path}.{key}: additional property is not allowed")

            for name, item_schema in properties.items():
                if name in value and isinstance(item_schema, Mapping):
                    errors.extend(validate_json_schema(value[name], item_schema, f"{path}.{name}"))

    return errors


class UniversalToolExecutor:
    """Run every provider's tool call through one validation/policy boundary."""

    def __init__(self, registry, *, clock: Callable[[], float] = time.time):
        self.registry = registry
        self.clock = clock

    def execute(
        self,
        call: UniversalToolCall | Mapping[str, Any],
        *,
        authorize: Optional[Callable[[UniversalToolDefinition, UniversalToolCall], Any]] = None,
        policy: Optional[Callable[[UniversalToolDefinition, UniversalToolCall], Any]] = None,
        approval: Optional[Callable[[UniversalToolDefinition, UniversalToolCall], Any]] = None,
    ) -> dict[str, Any]:
        if not isinstance(call, UniversalToolCall):
            call = UniversalToolCall(
                tool_name=str(call.get("tool_name") or call.get("name") or ""),
                arguments=dict(call.get("arguments") or {}),
                call_id=call.get("call_id"),
                transport=str(call.get("transport") or "internal"),
                trace_id=call.get("trace_id"),
                invocation_id=call.get("invocation_id"),
                user_id=call.get("user_id"),
                approved=bool(call.get("approved")),
                metadata=dict(call.get("metadata") or {}),
            )

        raw_definition = self.registry.get_universal_definition(call.tool_name)
        if raw_definition is None:
            return UniversalToolResult(
                False,
                error=f"Unknown tool: {call.tool_name}",
                metadata={"tool": call.tool_name, "phase": "lookup"},
            ).to_mapping()

        definition = (
            raw_definition
            if isinstance(raw_definition, UniversalToolDefinition)
            else UniversalToolDefinition.from_mapping(call.tool_name, raw_definition)
        )
        base_meta = {
            "tool": definition.name,
            "transport": call.transport,
            "call_id": call.call_id,
            "trace_id": call.trace_id,
            "invocation_id": call.invocation_id,
            "started_at": self.clock(),
        }

        if call.transport not in definition.supported_transports and call.transport != "internal":
            return UniversalToolResult(
                False,
                error=f"Tool '{call.tool_name}' does not support transport '{call.transport}'",
                metadata={**base_meta, "phase": "transport"},
            ).to_mapping()

        errors = validate_json_schema(call.arguments, definition.input_schema)
        if errors:
            return UniversalToolResult(
                False,
                error="Input validation failed: " + "; ".join(errors[:8]),
                metadata={**base_meta, "phase": "validation", "validation_errors": errors},
            ).to_mapping()

        try:
            if authorize is not None and authorize(definition, call) is False:
                raise PermissionError("Tool authorization denied")
            if policy is not None and policy(definition, call) is False:
                raise PermissionError("Tool policy denied")
        except Exception as exc:
            return UniversalToolResult(
                False,
                error=str(exc),
                metadata={**base_meta, "phase": "authorization"},
            ).to_mapping()

        if definition.requires_approval and not call.approved:
            try:
                approved = approval(definition, call) if approval is not None else False
            except Exception as exc:
                return UniversalToolResult(
                    False,
                    error=str(exc),
                    metadata={**base_meta, "phase": "approval"},
                ).to_mapping()
            if approved is not True:
                return UniversalToolResult(
                    False,
                    error="Tool approval required",
                    metadata={**base_meta, "phase": "approval_required"},
                ).to_mapping()

        started = self.clock()
        try:
            target = dict(definition.executor)
            target_type = str(target.get("type") or "local")
            if target_type in {"remote_local_agent", "local_agent"}:
                raw_result = self._execute_remote(
                    definition,
                    call,
                    target,
                )
            else:
                raw_result = self.registry.execute(
                    call.tool_name,
                    dict(call.arguments),
                    context={
                        "call": call,
                        "tool": definition,
                        "user_id": call.user_id,
                    },
                )
            result = self._normalize_result(raw_result, definition, call)
            result["metadata"].update(
                {
                    "executor": target_type,
                    "duration_ms": round(max(0.0, self.clock() - started) * 1000, 2),
                }
            )
            return result
        except PermissionError as exc:
            return UniversalToolResult(
                False,
                error=str(exc),
                metadata={
                    **base_meta,
                    "phase": "authorization",
                    "executor": str((definition.executor or {}).get("type") or "local"),
                    "duration_ms": round(max(0.0, self.clock() - started) * 1000, 2),
                },
            ).to_mapping()
        except ValueError as exc:
            return UniversalToolResult(
                False,
                error=str(exc),
                metadata={
                    **base_meta,
                    "phase": "validation",
                    "executor": str((definition.executor or {}).get("type") or "local"),
                    "duration_ms": round(max(0.0, self.clock() - started) * 1000, 2),
                },
            ).to_mapping()
        except Exception as exc:
            return UniversalToolResult(
                False,
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
        if redact_result_fields:
            for entry in reversed(trace.trace.get("tool_calls", [])):
                if entry.get("call_id") == call.call_id:
                    traced_result = entry.get("result")
                    if isinstance(traced_result, dict):
                        for key in redact_result_fields:
                            if key in traced_result:
                                traced_result[key] = "<redacted>"
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
        if not agent_id:
            raise ValueError(f"Tool '{definition.name}' has no remote agent target")

        timeout_seconds = float(target.get("timeout_seconds") or 30.0)
        job_id = enqueue_local_tool_job(
            agent_id,
            definition.name,
            dict(call.arguments),
            metadata={
                **dict(call.metadata),
                "transport": call.transport,
                "risk_level": definition.risk_level,
            },
            trace_id=call.trace_id,
            invocation_id=call.invocation_id,
        )
        result = wait_for_local_tool_job(
            job_id,
            timeout_seconds=timeout_seconds,
        )
        if result is None:
            raise TimeoutError(
                f"Local agent job {job_id} did not complete within {timeout_seconds:g}s"
            )
        return result

    @staticmethod
    def _normalize_result(
        raw_result: Any,
        definition: UniversalToolDefinition,
        call: UniversalToolCall,
    ) -> dict[str, Any]:
        if isinstance(raw_result, Mapping) and "success" in raw_result:
            success = bool(raw_result.get("success"))
            if success and "data" not in raw_result:
                data = {
                    key: value
                    for key, value in raw_result.items()
                    if key not in {"success", "error", "metadata"}
                }
            else:
                data = raw_result.get("data")
            error = raw_result.get("error")
            metadata = dict(raw_result.get("metadata") or {})
        elif isinstance(raw_result, Mapping) and raw_result.get("error"):
            success = False
            data = None
            error = str(raw_result.get("error"))
            metadata = {"phase": "execution"}
        else:
            success = True
            data = raw_result
            error = None
            metadata = {}

        return UniversalToolResult(
            success=success,
            data=data,
            error=str(error) if error is not None else None,
            metadata={
                "tool": definition.name,
                "transport": call.transport,
                **metadata,
            },
        ).to_mapping()


__all__ = [
    "UniversalToolCall",
    "UniversalToolDefinition",
    "UniversalToolExecutor",
    "UniversalToolResult",
    "validate_json_schema",
]

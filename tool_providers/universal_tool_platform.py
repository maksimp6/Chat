"""Provider-agnostic tool contracts and execution pipeline for Logos.

The module is intentionally independent from Flask/MCP/Android presentation code.
Adapters create a UniversalToolCall and hand it to UniversalToolExecutor.
"""

from __future__ import annotations

import math
import time
import warnings
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from functools import lru_cache
from typing import Any

from tool_providers._vendor.restrictions import restrictions_lifted
from tool_providers.host import enqueue_local_tool_job, wait_for_local_tool_job
from tool_providers.read_only_policy import ReadOnlyToolPolicy


def _warn_deprecated_execution_hooks(hooks: Mapping[str, Any]) -> None:
    """Compatibility adapter for callbacks removed from shared tool execution."""
    unsupported = set(hooks) - {"authorize", "policy", "approval"}
    if unsupported:
        names = ", ".join(sorted(unsupported))
        raise TypeError(f"Unexpected tool execution keyword arguments: {names}")
    if hooks:
        warnings.warn(
            "Tool execution callbacks authorize, policy and approval are deprecated "
            "and have no effect; remove these keyword arguments",
            DeprecationWarning,
            stacklevel=3,
        )


# Public name for other units; they reach it through their host ports.
warn_deprecated_execution_hooks = _warn_deprecated_execution_hooks


@dataclass(frozen=True)
class UniversalToolDefinition:
    name: str
    description: str
    input_schema: Mapping[str, Any] | bool
    output_schema: Mapping[str, Any] | bool
    capabilities: tuple[str, ...] = ()
    risk_level: str = "medium"
    read_only: bool = False
    requires_approval: bool = True
    supported_transports: tuple[str, ...] = ("responses_api", "local_agent")
    executor: Mapping[str, Any] = field(default_factory=lambda: {"type": "local"})
    title: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(
        cls, name: str, cfg: Mapping[str, Any]
    ) -> "UniversalToolDefinition":
        return cls(
            name=name,
            title=str(cfg.get("title") or name),
            description=str(cfg.get("description") or ""),
            input_schema=_contract_schema(
                cfg,
                ("inputSchema", "input_schema", "parameters"),
                {
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": False,
                },
            ),
            output_schema=_contract_schema(
                cfg, ("outputSchema", "output_schema"), {"type": "object"}
            ),
            capabilities=tuple(str(x) for x in (cfg.get("capabilities") or ())),
            risk_level=str(cfg.get("risk_level") or "medium"),
            read_only=bool(cfg.get("read_only", False)),
            requires_approval=bool(cfg.get("requires_approval", True)),
            supported_transports=tuple(
                str(x)
                for x in (
                    cfg.get("supported_transports") or ("responses_api", "local_agent")
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
    call_id: str | None = None
    transport: str = "internal"
    trace_id: str | None = None
    invocation_id: str | None = None
    user_id: str | None = None
    approved: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class UniversalToolResult:
    success: bool
    data: Any = None
    error: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_mapping(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "data": self.data if self.success else None,
            "error": self.error,
            "metadata": dict(self.metadata),
        }


def _contract_schema(
    cfg: Mapping[str, Any], names: tuple[str, ...], default: dict[str, Any]
) -> Mapping[str, Any] | bool:
    # Empty objects and False are real schemas, rather than missing settings.
    for name in names:
        if name in cfg and cfg[name] is not None:
            schema = cfg[name]
            return dict(schema) if isinstance(schema, Mapping) else schema
    return default


def _value_path(path: str, parts) -> str:
    for part in parts:
        path += f"[{part}]" if isinstance(part, int) else f".{part}"
    return path


def _json_data_errors(value: Any, path: str) -> list[str]:
    """Reject non-JSON numbers and cyclic containers before schema evaluation."""
    stack = [(path, value, False)]
    active: set[int] = set()
    errors = []
    while stack:
        current_path, item, leaving = stack.pop()
        if leaving:
            active.remove(id(item))
        elif isinstance(item, float) and not math.isfinite(item):
            errors.append(f"{current_path}: number must be finite")
        elif isinstance(item, (Mapping, list)):
            if id(item) in active:
                errors.append(f"{current_path}: cyclic data is not JSON")
                continue
            active.add(id(item))
            stack.append((current_path, item, True))
            if isinstance(item, Mapping):
                for key, child in reversed(list(item.items())):
                    if not isinstance(key, str):
                        errors.append(f"{current_path}: object keys must be strings")
                    else:
                        stack.append((_value_path(current_path, (key,)), child, False))
            else:
                for index in range(len(item) - 1, -1, -1):
                    stack.append(
                        (_value_path(current_path, (index,)), item[index], False)
                    )
        elif item is not None and not isinstance(item, (str, bool, int, float)):
            errors.append(f"{current_path}: value is not JSON data")
    return errors


@lru_cache(maxsize=1)
def _schema_runtime():
    # Keep the contract module importable by lightweight agents. Validation still
    # fails closed when their deployment omitted the declared jsonschema dependency.
    from jsonschema import Draft202012Validator
    from jsonschema.exceptions import SchemaError
    from jsonschema.validators import extend, validator_for
    from referencing import Registry
    from referencing.exceptions import Unresolvable

    return (
        Draft202012Validator,
        SchemaError,
        extend,
        validator_for,
        Registry,
        Unresolvable,
    )


def validate_json_schema(
    value: Any, schema: Mapping[str, Any] | bool, path: str = "$"
) -> list[str]:
    """Validate tool contracts with jsonschema; default to JSON Schema 2020-12.

    Explicit, known draft declarations use their corresponding library validator.
    References resolve only within the supplied schema, never via network or files.
    Error strings retain the existing path prefix without exposing argument values.
    """
    if not isinstance(schema, (Mapping, bool)):
        return [f"{path}: schema must be an object or boolean"]
    data_errors = _json_data_errors(value, path)
    if data_errors:
        return data_errors
    schema_errors = _json_data_errors(schema, path)
    if schema_errors:
        return [f"{path}: schema is not JSON data"]
    try:
        default, SchemaError, extend, validator_for, Registry, Unresolvable = (
            _schema_runtime()
        )
    except ImportError:
        return [f"{path}: jsonschema validation dependency is unavailable"]
    try:
        validator_type = validator_for(schema, default=default)
        if isinstance(schema, Mapping) and "$schema" in schema:
            validator_type = validator_for(schema, default=None)
            if validator_type is None:
                return [f"{path}: unsupported JSON Schema draft"]
        validator_type.check_schema(schema)
        # UniversalToolCall permits Mapping implementations in addition to dict.
        type_checker = validator_type.TYPE_CHECKER.redefine(
            "object", lambda _checker, instance: isinstance(instance, Mapping)
        )
        validator = extend(validator_type, type_checker=type_checker)(
            schema, registry=Registry()
        )
        errors = []
        for error in validator.iter_errors(value):
            error_path = _value_path(path, error.absolute_path)
            keyword = error.validator or "schema"
            # jsonschema's full messages can include repr(instance), including secrets.
            errors.append(f"{error_path}: does not satisfy {keyword}")
        return errors
    except SchemaError as exc:
        return [f"{_value_path(path, exc.absolute_path)}: invalid JSON Schema"]
    except Unresolvable:
        return [f"{path}: schema reference cannot be resolved locally"]
    except (TypeError, ValueError, RecursionError):
        return [f"{path}: invalid or recursive JSON Schema"]


def tool_call_requires_approval(
    definition_or_cfg: UniversalToolDefinition | Mapping[str, Any],
    arguments: Mapping[str, Any] | None = None,
) -> bool:
    """Return whether this concrete tool call crosses an approval boundary."""
    if restrictions_lifted():
        return False
    if isinstance(definition_or_cfg, UniversalToolDefinition):
        requires_approval = definition_or_cfg.requires_approval
        metadata = dict(definition_or_cfg.metadata or {})
    else:
        requires_approval = bool(definition_or_cfg.get("requires_approval", True))
        metadata = dict(definition_or_cfg.get("metadata") or {})

    approval_actions = {
        str(action)
        for action in (metadata.get("approval_actions") or ())
        if str(action)
    }
    if approval_actions:
        action = str((arguments or {}).get("action") or "")
        if action in approval_actions:
            return True

    return requires_approval


def _normalize_responses_arguments(value: Any, schema: Mapping[str, Any] | bool) -> Any:
    """Restore omitted optional fields encoded as null by strict Responses schemas.

    Null remains a value when the original contract accepts it. Required values,
    unknown fields and invalid types are left for ordinary schema validation.
    """
    if not isinstance(schema, Mapping):
        return value
    any_of = schema.get("anyOf")
    if isinstance(any_of, list):
        for option in any_of:
            if isinstance(option, (Mapping, bool)):
                candidate = _normalize_responses_arguments(value, option)
                if not validate_json_schema(candidate, option):
                    return candidate
        return value
    properties = schema.get("properties")
    if isinstance(value, Mapping) and isinstance(properties, Mapping):
        required = set(schema.get("required") or [])
        normalized = dict(value)
        for name, item_schema in properties.items():
            if name not in value or not isinstance(item_schema, (Mapping, bool)):
                continue
            if (
                value[name] is None
                and name not in required
                and validate_json_schema(None, item_schema)
            ):
                normalized.pop(name)
            else:
                normalized[name] = _normalize_responses_arguments(
                    value[name], item_schema
                )
        return normalized
    item_schema = schema.get("items")
    if isinstance(value, list) and isinstance(item_schema, (Mapping, bool)):
        return [_normalize_responses_arguments(item, item_schema) for item in value]
    return value


class UniversalToolExecutor:
    """Validate, execute and normalize every provider's tool call."""

    def __init__(self, registry, *, clock: Callable[[], float] = time.time):
        self.registry = registry
        self.clock = clock

    def execute(
        self,
        call: UniversalToolCall | Mapping[str, Any],
        *,
        server_configs: list[dict[str, Any]] | None = None,
        **legacy_hooks: Any,
    ) -> dict[str, Any]:
        _warn_deprecated_execution_hooks(legacy_hooks)
        if not isinstance(call, UniversalToolCall):
            if not isinstance(call, Mapping):
                return UniversalToolResult(
                    False,
                    error="Tool call must be an object",
                    metadata={"phase": "validation"},
                ).to_mapping()
            call = UniversalToolCall(
                tool_name=str(call.get("tool_name") or call.get("name") or ""),
                arguments=call.get("arguments", {}),
                call_id=call.get("call_id"),
                transport=str(call.get("transport") or "internal"),
                trace_id=call.get("trace_id"),
                invocation_id=call.get("invocation_id"),
                # A mapping never names the owner; the host sets user_id on UniversalToolCall.
                user_id=None,
                # A mapping never grants approval; the host sets it on UniversalToolCall.
                approved=False,
                metadata=call.get("metadata") or {},
            )

        if not isinstance(call.arguments, Mapping) or any(
            not isinstance(key, str) for key in call.arguments
        ):
            return UniversalToolResult(
                False,
                error="Tool arguments must be an object with string keys",
                metadata={
                    "tool": call.tool_name,
                    "phase": "validation",
                    "call_id": call.call_id,
                },
            ).to_mapping()

        if not isinstance(call.metadata, Mapping):
            return UniversalToolResult(
                False,
                error="Tool metadata must be an object",
                metadata={
                    "tool": call.tool_name,
                    "phase": "validation",
                    "call_id": call.call_id,
                },
            ).to_mapping()

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

        if (
            call.transport not in definition.supported_transports
            and call.transport != "internal"
        ):
            return UniversalToolResult(
                False,
                error=f"Tool '{call.tool_name}' does not support transport '{call.transport}'",
                metadata={**base_meta, "phase": "transport"},
            ).to_mapping()

        if call.transport == "responses_api":
            call = replace(
                call,
                arguments=_normalize_responses_arguments(
                    call.arguments, definition.input_schema
                ),
            )
        errors = validate_json_schema(call.arguments, definition.input_schema)
        if errors:
            return UniversalToolResult(
                False,
                error="Input validation failed: " + "; ".join(errors[:8]),
                metadata={
                    **base_meta,
                    "phase": "validation",
                    "validation_errors": errors,
                },
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
                    **(
                        {"server_configs": server_configs}
                        if server_configs is not None
                        else {}
                    ),
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
                    "error_type": type(exc).__name__,
                    "executor": dict(definition.executor),
                    "duration_ms": round(max(0.0, self.clock() - started) * 1000, 2),
                },
            ).to_mapping()

    def execute_with_trace(
        self,
        call: UniversalToolCall,
        trace,
        *,
        server_configs: list[dict[str, Any]] | None = None,
        **legacy_hooks: Any,
    ) -> dict[str, Any]:
        if trace is None:
            return self.execute(call, server_configs=server_configs, **legacy_hooks)

        definition = self.registry.get_universal_definition(call.tool_name)
        arguments = (
            dict(call.arguments)
            if isinstance(call.arguments, Mapping)
            else call.arguments
        )
        trace_arguments = (
            dict(arguments) if isinstance(arguments, Mapping) else arguments
        )
        if definition is not None and isinstance(trace_arguments, Mapping):
            redact = set(
                dict(definition.metadata or {}).get("trace_redact_arguments") or ()
            )
            for key in redact:
                if key in trace_arguments:
                    trace_arguments[key] = "<redacted>"

        traced_call = replace(
            call,
            metadata=(
                {**dict(call.metadata), "execution_trace": trace}
                if isinstance(call.metadata, Mapping)
                else call.metadata
            ),
        )
        result = trace.track_tool_execution(
            call.tool_name,
            arguments,
            lambda: self.execute(
                traced_call, server_configs=server_configs, **legacy_hooks
            ),
            call_id=call.call_id,
            step=None,
            trace_arguments=trace_arguments,
        )

        redact_result_fields = (
            set(dict(definition.metadata or {}).get("trace_redact_result_fields") or ())
            if definition is not None
            else set()
        )

        def redact_fields(value):
            if isinstance(value, dict):
                return {
                    key: "<redacted>"
                    if key in redact_result_fields
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

        agent_id = str(
            target.get("agent_id") or target.get("target_agent") or ""
        ).strip()
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
                "user_id": call.user_id,
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
            has_error = (
                raw_result.get("error") is not None or raw_result.get("isError") is True
            )
            success = raw_result.get("success") is True and not has_error
            if success and "data" not in raw_result:
                data = {
                    key: value
                    for key, value in raw_result.items()
                    if key not in {"success", "error", "metadata"}
                }
            else:
                data = raw_result.get("data")
            error = (
                None
                if success
                else raw_result.get("error")
                or (
                    raw_result.get("content")
                    if raw_result.get("isError") is True
                    else None
                )
                or "Tool execution failed"
            )
            if not isinstance(raw_result.get("success"), bool) and not has_error:
                error = "Tool returned a non-boolean success flag"
            metadata = dict(raw_result.get("metadata") or {})
        elif isinstance(raw_result, Mapping) and (
            raw_result.get("error") is not None or raw_result.get("isError") is True
        ):
            success = False
            data = None
            error = str(
                raw_result.get("error")
                or raw_result.get("content")
                or "Tool execution failed"
            )
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
    "ReadOnlyToolPolicy",
    "UniversalToolCall",
    "UniversalToolDefinition",
    "UniversalToolExecutor",
    "UniversalToolResult",
    "tool_call_requires_approval",
    "validate_json_schema",
    "warn_deprecated_execution_hooks",
]

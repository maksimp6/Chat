"""Strict API contracts shared by routes and tests.

The contract validator intentionally rejects unknown fields. API changes must be
made by changing the named contract first, then updating producers/consumers.
"""

from __future__ import annotations

from typing import Any, Mapping


class ContractViolation(ValueError):
    """Raised when an API payload does not satisfy its named contract."""


def _object(required=None, optional=None, *, additional=False):
    return {
        "type": "object",
        "required": dict(required or {}),
        "optional": dict(optional or {}),
        "additional": additional,
    }


def _list(items):
    return {"type": "array", "items": items}


def _nullable(spec):
    return {"type": "nullable", "item": spec}


def _enum(*values):
    return {"type": "enum", "values": tuple(values)}


STRING = {"type": "string"}
NONEMPTY_STRING = {"type": "string", "min_length": 1}
INTEGER = {"type": "integer"}
BOOLEAN = {"type": "boolean"}
ANY_JSON = {"type": "json"}
JSON_OBJECT = _object(additional=ANY_JSON)
NULLABLE_INTEGER = _nullable(INTEGER)
NULLABLE_STRING = _nullable(STRING)
NULLABLE_JSON = _nullable(ANY_JSON)

VIRTUAL_SERVER = _object(
    {
        "runtime": STRING,
        "cwd": STRING,
        "env": _object(additional=STRING),
        "memory_mb": INTEGER,
        "cpu_seconds": INTEGER,
        "command_timeout_seconds": INTEGER,
        "idle_timeout_seconds": INTEGER,
        "max_output_bytes": INTEGER,
    }
)

SESSION_PROFILE = _object(
    {
        "id": NONEMPTY_STRING,
        "name": NONEMPTY_STRING,
        "type": NONEMPTY_STRING,
        "model": STRING,
        "system_prompt": STRING,
        "tools": _list(STRING),
        "environment": JSON_OBJECT,
        "files": _list(STRING),
        "state": JSON_OBJECT,
        "virtual_server": VIRTUAL_SERVER,
        "template": BOOLEAN,
    }
)

SESSION = _object(
    {
        "id": NONEMPTY_STRING,
        "status": _enum("active", "completed", "failed", "cancelled"),
        "metadata": JSON_OBJECT,
        "created_at": INTEGER,
        "updated_at": INTEGER,
        "completed_at": NULLABLE_INTEGER,
    }
)

SESSION_DETAIL = _object(
    {
        "id": NONEMPTY_STRING,
        "status": _enum("active", "completed", "failed", "cancelled"),
        "metadata": JSON_OBJECT,
        "created_at": INTEGER,
        "updated_at": INTEGER,
        "completed_at": NULLABLE_INTEGER,
        "profile": _nullable(SESSION_PROFILE),
    }
)

INVOCATION_CONTEXT = _object(
    {
        "session_id": NONEMPTY_STRING,
        "conversation_id": NONEMPTY_STRING,
        "invocation_id": NONEMPTY_STRING,
        "trace_id": NONEMPTY_STRING,
        "user_id": NULLABLE_STRING,
        "metadata": JSON_OBJECT,
    }
)

INVOCATION_VISIBLE = _object(
    {
        "id": NONEMPTY_STRING,
        "session_id": NONEMPTY_STRING,
        "conversation_id": NONEMPTY_STRING,
        "trace_id": NONEMPTY_STRING,
        "status": _enum("created", "running", "completed", "failed", "cancelled"),
        "metadata": JSON_OBJECT,
        "result": NULLABLE_JSON,
        "error": NULLABLE_JSON,
        "created_at": INTEGER,
        "started_at": NULLABLE_INTEGER,
        "completed_at": NULLABLE_INTEGER,
    }
)

INVOCATION_STATUS = _object(
    {
        "id": NONEMPTY_STRING,
        "session_id": NONEMPTY_STRING,
        "conversation_id": NONEMPTY_STRING,
        "trace_id": NONEMPTY_STRING,
        "status": _enum("created", "running", "completed", "failed", "cancelled"),
        "created_at": INTEGER,
        "started_at": NULLABLE_INTEGER,
        "completed_at": NULLABLE_INTEGER,
        "error": NULLABLE_JSON,
    }
)

TRACE_CONTEXT = _object(
    {
        "invocation_id": NONEMPTY_STRING,
        "session_id": NONEMPTY_STRING,
        "conversation_id": NONEMPTY_STRING,
        "trace_id": NONEMPTY_STRING,
    },
    optional={
        "user_id": NONEMPTY_STRING,
        "owner_id": NONEMPTY_STRING,
        "provider_key_id": NONEMPTY_STRING,
    },
)

TRACE = _object(
    {
        "trace_id": NONEMPTY_STRING,
        "context": TRACE_CONTEXT,
        "responses": _list(ANY_JSON),
        "tool_calls": _list(ANY_JSON),
        "events": _list(ANY_JSON),
        "errors": _list(ANY_JSON),
    },
    optional={
        "schema_version": INTEGER,
        "created_at": {"type": "number"},
        "request": ANY_JSON,
        "timings": JSON_OBJECT,
        "billing": JSON_OBJECT,
        "provider_key": NULLABLE_JSON,
        "provider_keys": _list(ANY_JSON),
    },
)

CONTRACTS = {
    "runtime.profile.list.response": _object({"profiles": _list(SESSION_PROFILE)}),
    "runtime.profile.response": SESSION_PROFILE,
    "runtime.profile.clone.request": _object(optional={"name": _nullable(NONEMPTY_STRING)}),
    "runtime.profile.clone.response": _object({"session": SESSION, "profile": SESSION_PROFILE}),
    "runtime.session.create.request": _object(optional={"metadata": _nullable(JSON_OBJECT)}),
    "runtime.session.response": SESSION,
    "runtime.session.detail.response": SESSION_DETAIL,
    "runtime.invocation.create.request": _object(
        {"conversation_id": NONEMPTY_STRING},
        optional={"metadata": _nullable(JSON_OBJECT)},
    ),
    "runtime.invocation.create.response": INVOCATION_CONTEXT,
    "runtime.invocation.response": INVOCATION_VISIBLE,
    "runtime.invocation.status.response": INVOCATION_STATUS,
    "runtime.invocation.trace.response": TRACE,
    "runtime.error.basic": _object({"error": NONEMPTY_STRING}),
    "runtime.error.message": _object({"error": NONEMPTY_STRING, "message": NONEMPTY_STRING}),
    "runtime.error.invalid_request": _object(
        {
            "error": _enum("invalid_request"),
            "message": NONEMPTY_STRING,
        }
    ),
}


def validate_api_contract(name: str, payload: Any) -> Any:
    try:
        spec = CONTRACTS[name]
    except KeyError as exc:
        raise ContractViolation(f"unknown API contract: {name}") from exc
    _validate(spec, payload, "$")
    return payload


def _validate(spec: Mapping[str, Any], value: Any, path: str) -> None:
    kind = spec["type"]

    if kind == "nullable":
        if value is None:
            return
        _validate(spec["item"], value, path)
        return

    if kind == "json":
        _validate_json(value, path)
        return

    if kind == "enum":
        if value not in spec["values"]:
            raise ContractViolation(
                f"{path}: expected one of {list(spec['values'])!r}, got {value!r}"
            )
        return

    if kind == "string":
        if not isinstance(value, str):
            raise ContractViolation(f"{path}: expected string, got {type(value).__name__}")
        if spec.get("min_length") and len(value.strip()) < spec["min_length"]:
            raise ContractViolation(f"{path}: string must not be empty")
        return

    if kind == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ContractViolation(f"{path}: expected integer, got {type(value).__name__}")
        return

    if kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ContractViolation(f"{path}: expected number, got {type(value).__name__}")
        return

    if kind == "boolean":
        if not isinstance(value, bool):
            raise ContractViolation(f"{path}: expected boolean, got {type(value).__name__}")
        return

    if kind == "array":
        if not isinstance(value, list):
            raise ContractViolation(f"{path}: expected array, got {type(value).__name__}")
        for index, item in enumerate(value):
            _validate(spec["items"], item, f"{path}[{index}]")
        return

    if kind == "object":
        if not isinstance(value, dict):
            raise ContractViolation(f"{path}: expected object, got {type(value).__name__}")

        required = spec.get("required", {})
        optional = spec.get("optional", {})
        missing = sorted(set(required) - set(value))
        if missing:
            raise ContractViolation(f"{path}: missing required fields: {', '.join(missing)}")

        known = set(required) | set(optional)
        extras = sorted(set(value) - known)
        additional = spec.get("additional", False)
        if extras and additional is False:
            raise ContractViolation(f"{path}: unexpected fields: {', '.join(extras)}")

        for key, field_spec in required.items():
            _validate(field_spec, value[key], f"{path}.{key}")
        for key, field_spec in optional.items():
            if key in value:
                _validate(field_spec, value[key], f"{path}.{key}")
        if additional is not False:
            for key in extras:
                _validate(additional, value[key], f"{path}.{key}")
        return

    raise ContractViolation(f"{path}: unsupported contract type {kind!r}")


def _validate_json(value: Any, path: str) -> None:
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, int) and not isinstance(value, bool):
        return
    if isinstance(value, float):
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json(item, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ContractViolation(f"{path}: JSON object keys must be strings")
            _validate_json(item, f"{path}.{key}")
        return
    raise ContractViolation(f"{path}: value is not JSON-compatible: {type(value).__name__}")

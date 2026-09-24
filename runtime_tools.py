"""Universal tool definitions backed by the SSH Runtime."""
from __future__ import annotations

from typing import Any

from ssh_runtime import SSHRuntime, SSHRuntimeError

runtime = SSHRuntime()


def _trace_event(cfg: dict | None, event_type: str, payload: dict) -> None:
    context = _universal_context(cfg)
    call = context.get("call") if isinstance(context, dict) else None
    metadata = getattr(call, "metadata", {}) if call is not None else {}
    trace = metadata.get("execution_trace") if isinstance(metadata, dict) else None
    if trace is not None and hasattr(trace, "add_event"):
        trace.add_event(event_type, payload)


def _universal_context(cfg: dict | None) -> dict:
    return cfg.get("_universal_context") if isinstance(cfg, dict) else {}

def _trusted_identity(cfg: dict | None) -> str | None:
    context = _universal_context(cfg)
    call = context.get("call") if isinstance(context, dict) else None
    identity = getattr(call, "user_id", None) if call is not None else None
    return str(identity).strip() if identity is not None and str(identity).strip() else None

def _runtime_args(args: dict, cfg: dict | None) -> dict:
    return {
        "target": str(args.get("target") or ""),
        "linux_user": None,
        "identity_id": _trusted_identity(cfg),
        "timeout_seconds": args.get("timeout_seconds"),
    }


def ssh_runtime_exec(args: dict, cfg: dict | None = None) -> dict[str, Any]:
    runtime_args = _runtime_args(args, cfg)
    command = str(args.get("command") or "")
    _trace_event(cfg, "runtime_started", {
        "runtime": "ssh",
        "operation": "execute",
        "target": runtime_args["target"],
        "identity_id": runtime_args["identity_id"],
    })
    try:
        result = runtime.execute(command=command, **runtime_args)
    except SSHRuntimeError as exc:
        _trace_event(cfg, "runtime_failed", {
            "runtime": "ssh",
            "operation": "execute",
            "target": runtime_args["target"],
            "identity_id": runtime_args["identity_id"],
            "error": str(exc),
        })
        return {"success": False, "error": str(exc)}

    _trace_event(cfg, "runtime_finished", {
        "runtime": "ssh",
        "operation": "execute",
        "target": runtime_args["target"],
        "linux_user": result.get("linux_user"),
        "exit_code": result.get("exit_code"),
        "success": result.get("success"),
    })
    return result


def ssh_runtime_read_file(args: dict, cfg: dict | None = None) -> dict[str, Any]:
    runtime_args = _runtime_args(args, cfg)
    _trace_event(cfg, "runtime_started", {
        "runtime": "ssh",
        "operation": "read_file",
        "target": runtime_args["target"],
        "identity_id": runtime_args["identity_id"],
    })
    try:
        result = runtime.read_file(path=str(args.get("path") or ""), **runtime_args)
    except SSHRuntimeError as exc:
        _trace_event(cfg, "runtime_failed", {
            "runtime": "ssh",
            "operation": "read_file",
            "target": runtime_args["target"],
            "linux_user": runtime_args["linux_user"],
            "error": str(exc),
        })
        return {"success": False, "error": str(exc)}

    _trace_event(cfg, "runtime_finished", {
        "runtime": "ssh",
        "operation": "read_file",
        "target": runtime_args["target"],
        "linux_user": result.get("linux_user"),
        "success": result.get("success"),
    })
    return result


def ssh_runtime_write_file(args: dict, cfg: dict | None = None) -> dict[str, Any]:
    runtime_args = _runtime_args(args, cfg)
    _trace_event(cfg, "runtime_started", {
        "runtime": "ssh",
        "operation": "write_file",
        "target": runtime_args["target"],
        "linux_user": runtime_args["linux_user"],
    })
    try:
        result = runtime.write_file(
            path=str(args.get("path") or ""),
            content=str(args.get("content") or ""),
            **runtime_args,
        )
    except SSHRuntimeError as exc:
        _trace_event(cfg, "runtime_failed", {
            "runtime": "ssh",
            "operation": "write_file",
            "target": runtime_args["target"],
            "linux_user": runtime_args["linux_user"],
            "error": str(exc),
        })
        return {"success": False, "error": str(exc)}

    _trace_event(cfg, "runtime_finished", {
        "runtime": "ssh",
        "operation": "write_file",
        "target": runtime_args["target"],
        "linux_user": result.get("linux_user"),
        "path": result.get("path"),
        "success": result.get("success"),
        "bytes_written": result.get("bytes_written"),
    })
    return result


_BASE_PROPERTIES = {
    "target": {"type": "string", "minLength": 1, "maxLength": 128},
    "timeout_seconds": {"anyOf": [{"type": "number"}, {"type": "null"}]},
}


def _schema(extra: dict) -> dict:
    properties = dict(_BASE_PROPERTIES)
    properties.update(extra)
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties.keys()),
        "additionalProperties": False,
    }


RUNTIME_TOOLS = {
    "ssh_runtime_exec": {
        "title": "SSH Runtime Execute",
        "description": (
            "Execute a shell command on a configured SSH target using the Linux user "
            "mapped from the trusted Alice identity."
        ),
        "parameters": _schema({
            "command": {"type": "string", "minLength": 1, "maxLength": 20000},
        }),
        "capabilities": ["runtime", "ssh", "linux", "remote_execution"],
        "risk_level": "high",
        "read_only": False,
        "requires_approval": True,
        "supported_transports": ["responses_api", "local_agent", "mcp"],
        "executor": {"type": "local"},
        "metadata": {"trace_redact_arguments": ["command"], "trace_redact_result_fields": ["stdout", "stderr", "command"]},
        "func": ssh_runtime_exec,
    },
    "ssh_runtime_read_file": {
        "title": "SSH Runtime Read File",
        "description": "Read a remote absolute path using the Linux user mapped from the trusted Alice identity.",
        "parameters": _schema({
            "path": {"type": "string", "minLength": 1, "maxLength": 4096},
        }),
        "capabilities": ["runtime", "ssh", "linux", "file_read"],
        "risk_level": "medium",
        "read_only": True,
        "requires_approval": False,
        "supported_transports": ["responses_api", "local_agent", "mcp"],
        "executor": {"type": "local"},
        "func": ssh_runtime_read_file,
    },
    "ssh_runtime_write_file": {
        "title": "SSH Runtime Write File",
        "description": "Atomically write a remote file using the Linux user mapped from the trusted Alice identity.",
        "parameters": _schema({
            "path": {"type": "string", "minLength": 1, "maxLength": 4096},
            "content": {"type": "string", "maxLength": 2000000},
        }),
        "capabilities": ["runtime", "ssh", "linux", "file_write"],
        "risk_level": "high",
        "read_only": False,
        "requires_approval": True,
        "supported_transports": ["responses_api", "local_agent", "mcp"],
        "executor": {"type": "local"},
        "metadata": {"trace_redact_arguments": ["content"], "trace_redact_result_fields": ["stdout", "stderr"]},
        "func": ssh_runtime_write_file,
    },
}


__all__ = ["RUNTIME_TOOLS"]

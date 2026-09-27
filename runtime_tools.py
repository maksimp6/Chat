"""Universal tool definitions backed by the SSH Runtime."""

from __future__ import annotations

import os
import subprocess
from typing import Any

from ssh_runtime import SSHRuntime, SSHRuntimeError
from ssh_runtime_settings import assert_operation_allowed, build_runtime

# Tests may inject a runtime instance. Production resolves the persisted
# SSH Runtime settings for every operation so settings changes take effect
# without restarting the application.
runtime = None


def _runtime_for_operation(
    operation: str, *, command: str | None = None, approved: bool = False
) -> SSHRuntime:
    if runtime is not None:
        return runtime
    assert_operation_allowed(operation, command=command, approved=approved)
    return build_runtime()


def _trace_event(cfg: dict | None, event_type: str, payload: dict) -> None:
    context = _universal_context(cfg)
    call = context.get("call") if isinstance(context, dict) else None
    metadata = getattr(call, "metadata", {}) if call is not None else {}
    trace = metadata.get("execution_trace") if isinstance(metadata, dict) else None
    if trace is not None and hasattr(trace, "add_event"):
        trace.add_event(event_type, payload)


def _universal_context(cfg: dict | None) -> dict:
    return cfg.get("_universal_context") if isinstance(cfg, dict) else {}


def _approved(cfg: dict | None) -> bool:
    context = _universal_context(cfg)
    call = context.get("call") if isinstance(context, dict) else None
    return bool(getattr(call, "approved", False)) if call is not None else False


def _trusted_identity(cfg: dict | None) -> str | None:
    context = _universal_context(cfg)
    call = context.get("call") if isinstance(context, dict) else None
    identity = getattr(call, "user_id", None) if call is not None else None
    return str(identity).strip() if identity is not None and str(identity).strip() else None


def _runtime_args(args: dict, cfg: dict | None) -> dict:
    return {
        "target": str(args.get("target") or ""),
        "identity_id": _trusted_identity(cfg),
        "timeout_seconds": args.get("timeout_seconds"),
    }


def _limit_local_output(value: str | bytes | None, max_bytes: int = 1048576) -> str:
    if value is None:
        return ""
    text = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value)
    raw = text.encode("utf-8", errors="replace")
    if len(raw) <= max_bytes:
        return text
    suffix = "\n...[output truncated]"
    suffix_bytes = suffix.encode("utf-8")
    budget = max(0, max_bytes - len(suffix_bytes))
    return raw[:budget].decode("utf-8", errors="ignore") + suffix


def local_runtime_exec(args: dict, cfg: dict | None = None) -> dict[str, Any]:
    """Execute a shell command on the host running the Alice Pro backend."""
    command = str(args.get("command") or "").strip()
    if not command:
        raise ValueError("Command is required")

    raw_timeout = args.get("timeout_seconds")
    timeout = 30.0 if raw_timeout is None else float(raw_timeout)
    if timeout < 1 or timeout > 300:
        raise ValueError("timeout_seconds must be between 1 and 300")

    raw_cwd = args.get("cwd")
    cwd = os.path.abspath(os.path.expanduser(str(raw_cwd))) if raw_cwd else os.getcwd()
    if not os.path.isdir(cwd):
        raise ValueError(f"Working directory does not exist: {cwd}")

    _trace_event(
        cfg,
        "runtime_started",
        {
            "runtime": "local",
            "operation": "execute",
            "cwd": cwd,
        },
    )

    try:
        completed = subprocess.run(
            ["/bin/bash", "-lc", command],
            shell=False,
            check=False,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        result = {
            "success": False,
            "error": f"Local command timed out after {timeout:g}s",
            "stdout": _limit_local_output(exc.stdout),
            "stderr": _limit_local_output(exc.stderr),
            "exit_code": None,
            "cwd": cwd,
            "timeout_seconds": timeout,
        }
        _trace_event(
            cfg,
            "runtime_failed",
            {
                "runtime": "local",
                "operation": "execute",
                "cwd": cwd,
                "error": result["error"],
            },
        )
        return result
    except OSError as exc:
        result = {
            "success": False,
            "error": f"Unable to start local shell: {exc}",
            "stdout": "",
            "stderr": "",
            "exit_code": None,
            "cwd": cwd,
            "timeout_seconds": timeout,
        }
        _trace_event(
            cfg,
            "runtime_failed",
            {
                "runtime": "local",
                "operation": "execute",
                "cwd": cwd,
                "error": result["error"],
            },
        )
        return result

    result = {
        "success": completed.returncode == 0,
        "stdout": _limit_local_output(completed.stdout),
        "stderr": _limit_local_output(completed.stderr),
        "exit_code": completed.returncode,
        "cwd": cwd,
        "timeout_seconds": timeout,
    }
    if completed.returncode != 0:
        result["error"] = f"Local command exited with code {completed.returncode}"

    _trace_event(
        cfg,
        "runtime_finished",
        {
            "runtime": "local",
            "operation": "execute",
            "cwd": cwd,
            "exit_code": completed.returncode,
            "success": completed.returncode == 0,
        },
    )
    return result


def ssh_runtime_exec(args: dict, cfg: dict | None = None) -> dict[str, Any]:
    runtime_args = _runtime_args(args, cfg)
    command = str(args.get("command") or "")
    _trace_event(
        cfg,
        "runtime_started",
        {
            "runtime": "ssh",
            "operation": "execute",
            "target": runtime_args["target"],
            "identity_id": runtime_args["identity_id"],
        },
    )
    try:
        result = _runtime_for_operation(
            "execute", command=command, approved=_approved(cfg)
        ).execute(command=command, **runtime_args)
    except SSHRuntimeError as exc:
        _trace_event(
            cfg,
            "runtime_failed",
            {
                "runtime": "ssh",
                "operation": "execute",
                "target": runtime_args["target"],
                "identity_id": runtime_args["identity_id"],
                "error": str(exc),
            },
        )
        return {"success": False, "error": str(exc)}

    _trace_event(
        cfg,
        "runtime_finished",
        {
            "runtime": "ssh",
            "operation": "execute",
            "target": runtime_args["target"],
            "linux_user": result.get("linux_user"),
            "exit_code": result.get("exit_code"),
            "success": result.get("success"),
        },
    )
    return result


def ssh_runtime_read_file(args: dict, cfg: dict | None = None) -> dict[str, Any]:
    runtime_args = _runtime_args(args, cfg)
    _trace_event(
        cfg,
        "runtime_started",
        {
            "runtime": "ssh",
            "operation": "read_file",
            "target": runtime_args["target"],
            "identity_id": runtime_args["identity_id"],
        },
    )
    try:
        result = _runtime_for_operation("read_file").read_file(
            path=str(args.get("path") or ""), **runtime_args
        )
    except SSHRuntimeError as exc:
        _trace_event(
            cfg,
            "runtime_failed",
            {
                "runtime": "ssh",
                "operation": "read_file",
                "target": runtime_args["target"],
                "identity_id": runtime_args["identity_id"],
                "error": str(exc),
            },
        )
        return {"success": False, "error": str(exc)}

    _trace_event(
        cfg,
        "runtime_finished",
        {
            "runtime": "ssh",
            "operation": "read_file",
            "target": runtime_args["target"],
            "linux_user": result.get("linux_user"),
            "success": result.get("success"),
        },
    )
    return result


def ssh_runtime_write_file(args: dict, cfg: dict | None = None) -> dict[str, Any]:
    runtime_args = _runtime_args(args, cfg)
    _trace_event(
        cfg,
        "runtime_started",
        {
            "runtime": "ssh",
            "operation": "write_file",
            "target": runtime_args["target"],
            "identity_id": runtime_args["identity_id"],
        },
    )
    try:
        result = _runtime_for_operation("write_file", approved=_approved(cfg)).write_file(
            path=str(args.get("path") or ""),
            content=str(args.get("content") or ""),
            **runtime_args,
        )
    except SSHRuntimeError as exc:
        _trace_event(
            cfg,
            "runtime_failed",
            {
                "runtime": "ssh",
                "operation": "write_file",
                "target": runtime_args["target"],
                "identity_id": runtime_args["identity_id"],
                "error": str(exc),
            },
        )
        return {"success": False, "error": str(exc)}

    _trace_event(
        cfg,
        "runtime_finished",
        {
            "runtime": "ssh",
            "operation": "write_file",
            "target": runtime_args["target"],
            "linux_user": result.get("linux_user"),
            "path": result.get("path"),
            "success": result.get("success"),
            "bytes_written": result.get("bytes_written"),
        },
    )
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
    "local_runtime_exec": {
        "title": "Local Runtime Execute",
        "description": (
            "Execute a shell command on the same Linux host that runs the Alice Pro backend. "
            "Use this for server-local diagnostics, maintenance and repository commands without SSH."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "minLength": 1, "maxLength": 20000},
                "cwd": {
                    "anyOf": [
                        {"type": "string", "minLength": 1, "maxLength": 4096},
                        {"type": "null"},
                    ]
                },
                "timeout_seconds": {
                    "anyOf": [
                        {"type": "number", "minimum": 1, "maximum": 300},
                        {"type": "null"},
                    ]
                },
            },
            "required": ["command", "cwd", "timeout_seconds"],
            "additionalProperties": False,
        },
        "capabilities": ["runtime", "linux", "local_execution"],
        "risk_level": "high",
        "read_only": False,
        "requires_approval": True,
        "supported_transports": ["responses_api", "local_agent", "mcp"],
        "executor": {"type": "local"},
        "metadata": {
            "trace_redact_arguments": ["command"],
            "trace_redact_result_fields": ["stdout", "stderr"],
        },
        "func": local_runtime_exec,
    },
    "ssh_runtime_exec": {
        "title": "SSH Runtime Execute",
        "description": (
            "Execute a shell command on a configured SSH target using the Linux user "
            "mapped from the trusted Alice identity."
        ),
        "parameters": _schema(
            {
                "command": {"type": "string", "minLength": 1, "maxLength": 20000},
            }
        ),
        "capabilities": ["runtime", "ssh", "linux", "remote_execution"],
        "risk_level": "high",
        "read_only": False,
        "requires_approval": True,
        "supported_transports": ["responses_api", "local_agent", "mcp"],
        "executor": {"type": "local"},
        # Keep command traces for auditability, but redact remote process output
        # fields because stdout/stderr are untrusted text.
        "metadata": {"trace_redact_result_fields": ["stdout", "stderr"]},
        "func": ssh_runtime_exec,
    },
    "ssh_runtime_read_file": {
        "title": "SSH Runtime Read File",
        "description": "Read a remote absolute path using the Linux user mapped from the trusted Alice identity.",
        "parameters": _schema(
            {
                "path": {"type": "string", "minLength": 1, "maxLength": 4096},
            }
        ),
        "capabilities": ["runtime", "ssh", "linux", "file_read"],
        "risk_level": "medium",
        "read_only": True,
        "requires_approval": False,
        "supported_transports": ["responses_api", "local_agent", "mcp"],
        "executor": {"type": "local"},
        "metadata": {"trace_redact_result_fields": ["stdout", "stderr"]},
        "func": ssh_runtime_read_file,
    },
    "ssh_runtime_write_file": {
        "title": "SSH Runtime Write File",
        "description": "Atomically write a remote file using the Linux user mapped from the trusted Alice identity.",
        "parameters": _schema(
            {
                "path": {"type": "string", "minLength": 1, "maxLength": 4096},
                "content": {"type": "string", "maxLength": 2000000},
            }
        ),
        "capabilities": ["runtime", "ssh", "linux", "file_write"],
        "risk_level": "high",
        "read_only": False,
        "requires_approval": True,
        "supported_transports": ["responses_api", "local_agent", "mcp"],
        "executor": {"type": "local"},
        "metadata": {
            "trace_redact_arguments": ["content"],
            "trace_redact_result_fields": ["stdout", "stderr"],
        },
        "func": ssh_runtime_write_file,
    },
}


__all__ = ["RUNTIME_TOOLS"]

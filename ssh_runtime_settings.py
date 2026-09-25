"""Configuration and policy boundary for the SSH Runtime plugin.

SSH connection details are stored as structured server-side configuration.
Private key material is never accepted by this module, only a server-side
reference to an existing key file.
"""
from __future__ import annotations

import copy
import json
import logging
import os
import subprocess
import time
from typing import Any, Mapping

from db import get_config, set_config
from ssh_runtime import SSHRuntime, SSHRuntimeError
from trace_manager import get_current_trace

logger = logging.getLogger("ssh_runtime_settings")

SETTINGS_KEY = "ssh_runtime_settings"
LAST_TEST_KEY = "ssh_runtime_last_test"

_DEFAULTS = {
    "enabled": False,
    "read_only": False,
    "allow_command_execution": True,
    "allow_write_operations": True,
    "max_output_bytes": 1048576,
    "known_hosts": None,
    "targets": {},
    "command_allowlist": [],
    "allow_privileged_operations": False,
    "approval_required_for_write": True,
    "approval_required_for_privileged": True,
}


def _env_bootstrap() -> dict[str, Any]:
    raw = os.getenv("ALICE_SSH_TARGETS_JSON", "").strip()
    targets: dict[str, Any] = {}
    if raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SSHRuntimeError("ALICE_SSH_TARGETS_JSON is not valid JSON") from exc
        if not isinstance(parsed, dict):
            raise SSHRuntimeError("ALICE_SSH_TARGETS_JSON must be an object")
        targets = parsed

    return {
        **copy.deepcopy(_DEFAULTS),
        "enabled": bool(targets),
        "known_hosts": os.getenv("ALICE_SSH_KNOWN_HOSTS") or None,
        "targets": targets,
    }


def _raw_settings() -> dict[str, Any]:
    stored = get_config(SETTINGS_KEY, None)
    if stored is None:
        return _env_bootstrap()
    if not isinstance(stored, Mapping):
        raise SSHRuntimeError("Stored SSH Runtime settings must be an object")
    result = copy.deepcopy(_DEFAULTS)
    result.update(dict(stored))
    result["targets"] = copy.deepcopy(stored.get("targets") or {})
    return result


def validate_settings(settings: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(settings, Mapping):
        raise SSHRuntimeError("SSH Runtime settings must be an object")

    result = copy.deepcopy(_DEFAULTS)
    result.update(dict(settings))

    for key in ("enabled", "read_only", "allow_command_execution", "allow_write_operations"):
        if not isinstance(result.get(key), bool):
            raise SSHRuntimeError(f"SSH Runtime setting '{key}' must be boolean")

    try:
        max_output = int(result.get("max_output_bytes", _DEFAULTS["max_output_bytes"]))
    except (TypeError, ValueError) as exc:
        raise SSHRuntimeError("max_output_bytes must be an integer") from exc
    if not 4096 <= max_output <= 10 * 1024 * 1024:
        raise SSHRuntimeError("max_output_bytes must be between 4096 and 10485760")
    result["max_output_bytes"] = max_output

    known_hosts = result.get("known_hosts")
    result["known_hosts"] = str(known_hosts).strip() if known_hosts else None

    allowlist = result.get("command_allowlist")
    if not isinstance(allowlist, (list, tuple)):
        raise SSHRuntimeError("command_allowlist must be an array")
    normalized_allowlist = []
    for pattern in allowlist:
        value = str(pattern or "").strip()
        if not value:
            raise SSHRuntimeError("command_allowlist cannot contain empty patterns")
        if len(value) > 512:
            raise SSHRuntimeError("command_allowlist pattern is too long")
        try:
            import re
            re.compile(value)
        except re.error as exc:
            raise SSHRuntimeError(f"Invalid command_allowlist pattern: {value}") from exc
        normalized_allowlist.append(value)
    result["command_allowlist"] = normalized_allowlist

    for key in ("allow_privileged_operations", "approval_required_for_write", "approval_required_for_privileged"):
        if not isinstance(result.get(key), bool):
            raise SSHRuntimeError(f"SSH Runtime setting '{key}' must be boolean")

    targets = result.get("targets")
    if not isinstance(targets, Mapping):
        raise SSHRuntimeError("SSH Runtime targets must be an object")
    result["targets"] = copy.deepcopy(dict(targets))

    if result["read_only"]:
        result["allow_command_execution"] = False
        result["allow_write_operations"] = False

    if result["enabled"] and not result["targets"]:
        raise SSHRuntimeError("At least one SSH target is required when Runtime is enabled")

    if result["enabled"]:
        if result["allow_command_execution"] and not result["command_allowlist"]:
            raise SSHRuntimeError("command_allowlist is required when SSH command execution is enabled")
        try:
            SSHRuntime(targets=result["targets"], known_hosts=result["known_hosts"])
        except SSHRuntimeError:
            raise
        except Exception as exc:
            raise SSHRuntimeError(f"Invalid SSH Runtime targets: {exc}") from exc

        for name, target in result["targets"].items():
            target_known_hosts = target.get("known_hosts") if isinstance(target, Mapping) else None
            if not (result["known_hosts"] or target_known_hosts):
                raise SSHRuntimeError(
                    f"SSH target '{name}' requires known_hosts for strict host-key verification"
                )
            if isinstance(target, Mapping):
                try:
                    for field in ("connect_timeout_seconds", "command_timeout_seconds"):
                        value = float(target.get(field, 30 if "command" in field else 10))
                        if value <= 0 or value > 3600:
                            raise SSHRuntimeError(
                                f"SSH target '{name}' has invalid {field}"
                            )
                except (TypeError, ValueError) as exc:
                    raise SSHRuntimeError(
                        f"SSH target '{name}' has invalid timeout"
                    ) from exc

    return result


def get_settings() -> dict[str, Any]:
    return validate_settings(_raw_settings())


def save_settings(settings: Mapping[str, Any]) -> dict[str, Any]:
    validated = validate_settings(settings)
    set_config(SETTINGS_KEY, validated)
    trace = get_current_trace()
    if trace is not None:
        trace.add_event("ssh_runtime_settings_updated", {
            "enabled": validated["enabled"],
            "targets": sorted(validated["targets"]),
            "read_only": validated["read_only"],
            "allow_command_execution": validated["allow_command_execution"],
            "allow_write_operations": validated["allow_write_operations"],
            "allow_privileged_operations": validated["allow_privileged_operations"],
            "command_allowlist_count": len(validated["command_allowlist"]),
        })
    logger.info(
        "SSH Runtime settings updated: enabled=%s targets=%s read_only=%s",
        validated["enabled"],
        sorted(validated["targets"]),
        validated["read_only"],
    )
    return validated


def public_settings() -> dict[str, Any]:
    settings = get_settings()
    result = copy.deepcopy(settings)
    last_test = get_config(LAST_TEST_KEY, None)
    if last_test is not None:
        result["last_test"] = copy.deepcopy(last_test)
    return result


def build_runtime() -> SSHRuntime:
    settings = get_settings()
    if not settings["enabled"]:
        raise SSHRuntimeError("SSH Runtime is disabled in settings")
    targets = copy.deepcopy(settings["targets"])
    for target in targets.values():
        if isinstance(target, Mapping):
            target.setdefault("max_output_bytes", settings["max_output_bytes"])
    return SSHRuntime(
        targets=targets,
        known_hosts=settings["known_hosts"],
    )


def command_matches_allowlist(command: str, allowlist: list[str]) -> bool:
    import re
    value = str(command or "").strip()
    return any(re.fullmatch(pattern, value) for pattern in allowlist)


def is_privileged_command(command: str) -> bool:
    import re
    value = str(command or "").strip()
    return bool(re.match(r"^(?:sudo|su|doas|pkexec)(?:\s|$)", value))


def assert_operation_allowed(operation: str, *, command: str | None = None, approved: bool = False) -> None:
    settings = get_settings()
    if not settings["enabled"]:
        raise SSHRuntimeError("SSH Runtime is disabled in settings")

    if operation == "execute":
        if not settings["allow_command_execution"]:
            raise SSHRuntimeError("SSH command execution is disabled in SSH Runtime settings")
        if command is None or not command_matches_allowlist(command, settings["command_allowlist"]):
            raise SSHRuntimeError("SSH command is not permitted by the configured command allowlist")
        if is_privileged_command(command):
            if not settings["allow_privileged_operations"]:
                raise SSHRuntimeError("Privileged SSH commands are disabled in SSH Runtime settings")
            if settings["approval_required_for_privileged"] and not approved:
                raise SSHRuntimeError("Approval is required for privileged SSH commands")
    if operation == "write_file":
        if not settings["allow_write_operations"]:
            raise SSHRuntimeError("SSH file write operations are disabled in SSH Runtime settings")
        if settings["approval_required_for_write"] and not approved:
            raise SSHRuntimeError("Approval is required for SSH file writes")
    if settings["read_only"] and operation != "read_file":
        raise SSHRuntimeError("SSH Runtime is configured as read-only")


def record_test_result(result: Mapping[str, Any]) -> None:
    set_config(LAST_TEST_KEY, dict(result))


def test_connection(target_name: str, identity_id: str | None) -> dict[str, Any]:
    runtime = build_runtime()
    target, user = runtime._resolve_target(target_name, identity_id)
    known_hosts = target.known_hosts or runtime._default_known_hosts
    started = time.perf_counter()
    argv = runtime._ssh_command(target, user, "true")
    try:
        completed = subprocess.run(
            argv,
            shell=False,
            check=False,
            capture_output=True,
            text=True,
            timeout=max(2, int(target.connect_timeout_seconds)) + 5,
        )
    except subprocess.TimeoutExpired as exc:
        result = {
            "success": False,
            "target": target.name,
            "host": target.host,
            "linux_user": user,
            "status": "timeout",
            "error": f"SSH connection timed out after {target.connect_timeout_seconds:g}s",
            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
        }
        record_test_result(result)
        raise SSHRuntimeError(result["error"]) from exc
    except OSError as exc:
        result = {
            "success": False,
            "target": target.name,
            "host": target.host,
            "linux_user": user,
            "status": "error",
            "error": f"Unable to start ssh: {exc}",
            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
        }
        record_test_result(result)
        raise SSHRuntimeError(result["error"]) from exc

    result = {
        "success": completed.returncode == 0,
        "target": target.name,
        "host": target.host,
        "linux_user": user,
        "status": "ok" if completed.returncode == 0 else "failed",
        "error": completed.stderr.strip() or None,
        "exit_code": completed.returncode,
        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
        "known_hosts_configured": bool(known_hosts),
    }
    record_test_result(result)
    trace = get_current_trace()
    if trace is not None:
        trace.add_event("ssh_runtime_connection_test", {
            "target": target.name,
            "linux_user": user,
            "success": result["success"],
            "status": result["status"],
            "duration_ms": result["duration_ms"],
        })
    logger.info(
        "SSH Runtime connection test: target=%s user=%s success=%s exit_code=%s",
        target.name,
        user,
        result["success"],
        completed.returncode,
    )
    if completed.returncode != 0:
        raise SSHRuntimeError(result["error"] or "SSH connection test failed")
    return result


__all__ = [
    "SETTINGS_KEY",
    "assert_operation_allowed",
    "command_matches_allowlist",
    "is_privileged_command",
    "build_runtime",
    "get_settings",
    "public_settings",
    "save_settings",
    "test_connection",
    "validate_settings",
]
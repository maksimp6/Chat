"""Configuration and policy boundary for the SSH Runtime plugin.

SSH connection details are stored as structured server-side configuration.
Private key material is never accepted by this module, only a server-side
reference to an existing key file.
"""

from __future__ import annotations

import copy
import json
import os
import re
from collections.abc import Mapping, MutableMapping
from typing import Any, cast

from remote_runtime._vendor.restrictions import restrictions_lifted
from remote_runtime.host import get_config
from remote_runtime.ssh import SSHRuntime, SSHRuntimeError

SETTINGS_KEY = "ssh_runtime_settings"
# Shell separators and substitutions. An allowlist pattern matches the whole command, so a command
# that chains a second one ("echo ok; sudo ...") would otherwise pass as the first.
_SHELL_CONTROL_RE = re.compile(r"[;&`\n\r]|\$\(")

_DEFAULTS: dict[str, Any] = {
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
    raw = os.environ.get("LOGOS_SSH_TARGETS_JSON", "").strip()
    targets: dict[str, Any] = {}
    if raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SSHRuntimeError("LOGOS_SSH_TARGETS_JSON is not valid JSON") from exc
        if not isinstance(parsed, dict):
            raise SSHRuntimeError("LOGOS_SSH_TARGETS_JSON must be an object")
        targets = parsed

    return {
        **copy.deepcopy(_DEFAULTS),
        "enabled": bool(targets),
        "known_hosts": os.environ.get("LOGOS_SSH_KNOWN_HOSTS") or None,
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

    for key in (
        "enabled",
        "read_only",
        "allow_command_execution",
        "allow_write_operations",
    ):
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
            re.compile(value)
        except re.error as exc:
            raise SSHRuntimeError(
                f"Invalid command_allowlist pattern: {value}"
            ) from exc
        normalized_allowlist.append(value)
    result["command_allowlist"] = normalized_allowlist

    for key in (
        "allow_privileged_operations",
        "approval_required_for_write",
        "approval_required_for_privileged",
    ):
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
        raise SSHRuntimeError(
            "At least one SSH target is required when Runtime is enabled"
        )

    if result["enabled"]:
        try:
            SSHRuntime(targets=result["targets"], known_hosts=result["known_hosts"])
        except SSHRuntimeError:
            raise
        except Exception as exc:
            raise SSHRuntimeError(f"Invalid SSH Runtime targets: {exc}") from exc

        for name, target in result["targets"].items():
            target_known_hosts = (
                target.get("known_hosts") if isinstance(target, Mapping) else None
            )
            if not (result["known_hosts"] or target_known_hosts):
                raise SSHRuntimeError(
                    f"SSH target '{name}' requires known_hosts for strict host-key verification",
                    code="ssh_known_hosts_missing",
                )
            if isinstance(target, Mapping):
                try:
                    for field in ("connect_timeout_seconds", "command_timeout_seconds"):
                        seconds = float(
                            target.get(field, 30 if "command" in field else 10)
                        )
                        if seconds <= 0 or seconds > 3600:
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


def public_settings() -> dict[str, Any]:
    return get_settings()


def build_runtime() -> SSHRuntime:
    return _runtime_from_settings(get_settings())


def _runtime_from_settings(settings: Mapping[str, Any]) -> SSHRuntime:
    if not settings["enabled"]:
        raise SSHRuntimeError(
            "SSH Runtime is disabled in settings", code="ssh_runtime_disabled"
        )
    targets = copy.deepcopy(settings["targets"])
    for target in targets.values():
        if isinstance(target, Mapping):
            cast("MutableMapping[str, Any]", target).setdefault(
                "max_output_bytes", settings["max_output_bytes"]
            )
    return SSHRuntime(
        targets=targets,
        known_hosts=settings["known_hosts"],
    )


def command_matches_allowlist(command: str, allowlist: list[str]) -> bool:
    value = str(command or "").strip()
    return any(re.fullmatch(pattern, value) for pattern in allowlist)


def is_privileged_command(command: str) -> bool:
    value = str(command or "").strip()
    # Every pipeline stage counts: a privileged stage after "|" is still privileged.
    return any(
        re.match(r"^(?:sudo|su|doas|pkexec)(?:\s|$)", stage.strip())
        for stage in value.split("|")
    )


def assert_operation_allowed(
    operation: str, *, command: str | None = None, approved: bool = False
) -> None:
    _check_operation(get_settings(), operation, command=command, approved=approved)


def runtime_for_operation(
    operation: str, *, command: str | None = None, approved: bool = False
) -> SSHRuntime:
    """Check the operation and build the runtime from one validated read of the settings."""
    settings = get_settings()
    _check_operation(settings, operation, command=command, approved=approved)
    return _runtime_from_settings(settings)


def _check_operation(
    settings: Mapping[str, Any],
    operation: str,
    *,
    command: str | None = None,
    approved: bool = False,
) -> None:
    if not settings["enabled"]:
        raise SSHRuntimeError(
            "SSH Runtime is disabled in settings", code="ssh_runtime_disabled"
        )

    if operation == "execute":
        if not settings["allow_command_execution"]:
            raise SSHRuntimeError(
                "SSH command execution is disabled in SSH Runtime settings"
            )
        if command is None:
            raise SSHRuntimeError(
                "SSH command is not permitted by the configured command allowlist"
            )
        if not restrictions_lifted():
            if _SHELL_CONTROL_RE.search(command):
                raise SSHRuntimeError(
                    "SSH command must not contain shell control operators"
                )
            if not command_matches_allowlist(command, settings["command_allowlist"]):
                raise SSHRuntimeError(
                    "SSH command is not permitted by the configured command allowlist"
                )
            if is_privileged_command(command):
                if not settings["allow_privileged_operations"]:
                    raise SSHRuntimeError(
                        "Privileged SSH commands are disabled in SSH Runtime settings"
                    )
                if settings["approval_required_for_privileged"] and not approved:
                    raise SSHRuntimeError(
                        "Approval is required for privileged SSH commands"
                    )
    if operation == "write_file" and not settings["allow_write_operations"]:
        raise SSHRuntimeError(
            "SSH file write operations are disabled in SSH Runtime settings"
        )
    if settings["read_only"] and operation != "read_file":
        raise SSHRuntimeError("SSH Runtime is configured as read-only")


__all__ = [
    "SETTINGS_KEY",
    "assert_operation_allowed",
    "build_runtime",
    "command_matches_allowlist",
    "get_settings",
    "is_privileged_command",
    "public_settings",
    "runtime_for_operation",
    "validate_settings",
]

"""SSH-backed runtime for executing commands and files as configured Linux users.

The runtime uses the system OpenSSH client. The selected SSH account is the
remote OS identity, so Linux permissions remain the authorization boundary.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Mapping, Optional


class SSHRuntimeError(RuntimeError):
    """Raised when an SSH runtime operation cannot be completed."""


@dataclass(frozen=True)
class SSHTarget:
    name: str
    host: str
    port: int = 22
    default_user: Optional[str] = None
    allowed_users: tuple[str, ...] = ()
    identity_file: Optional[str] = None
    known_hosts: Optional[str] = None
    identity_map: tuple[tuple[str, str], ...] = ()
    connect_timeout_seconds: float = 10.0
    command_timeout_seconds: float = 30.0


class SSHRuntime:
    """Execute commands and file operations through OpenSSH."""

    def __init__(
        self,
        targets: Optional[Mapping[str, Mapping[str, Any]]] = None,
        *,
        known_hosts: Optional[str] = None,
    ) -> None:
        self._targets = self._load_targets(targets)
        self._default_known_hosts = known_hosts or os.getenv("ALICE_SSH_KNOWN_HOSTS")

    @staticmethod
    def _load_targets(
        targets: Optional[Mapping[str, Mapping[str, Any]]],
    ) -> dict[str, SSHTarget]:
        if targets is None:
            text = os.getenv("ALICE_SSH_TARGETS_JSON", "").strip()
            if not text:
                return {}
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as exc:
                raise SSHRuntimeError("ALICE_SSH_TARGETS_JSON is not valid JSON") from exc
            if not isinstance(parsed, dict):
                raise SSHRuntimeError("ALICE_SSH_TARGETS_JSON must be an object")
            raw: Mapping[str, Mapping[str, Any]] = parsed
        else:
            raw = targets

        result: dict[str, SSHTarget] = {}
        for name, cfg in raw.items():
            if not isinstance(cfg, Mapping):
                raise SSHRuntimeError(f"SSH target '{name}' must be an object")
            host = str(cfg.get("host") or "").strip()
            if not host:
                raise SSHRuntimeError(f"SSH target '{name}' has no host")
            try:
                port = int(cfg.get("port", 22))
            except (TypeError, ValueError) as exc:
                raise SSHRuntimeError(f"SSH target '{name}' has invalid port") from exc
            if not 1 <= port <= 65535:
                raise SSHRuntimeError(f"SSH target '{name}' has invalid port")

            allowed = cfg.get("allowed_users") or ()
            if isinstance(allowed, str):
                allowed = (allowed,)
            if not isinstance(allowed, (list, tuple)):
                raise SSHRuntimeError(f"SSH target '{name}' has invalid allowed_users")

            identity_map = cfg.get("identity_map") or {}
            if not isinstance(identity_map, Mapping):
                raise SSHRuntimeError(f"SSH target '{name}' has invalid identity_map")
            normalized_identity_map = []
            for identity_id, linux_user in identity_map.items():
                normalized_identity_map.append(
                    (
                        str(identity_id).strip(),
                        SSHRuntime._validate_linux_user(str(linux_user).strip()),
                    )
                )

            result[str(name)] = SSHTarget(
                name=str(name),
                host=host,
                port=port,
                default_user=(str(cfg["default_user"]).strip() if cfg.get("default_user") else None),
                allowed_users=tuple(str(user).strip() for user in allowed if str(user).strip()),
                identity_file=(str(cfg["identity_file"]).strip() if cfg.get("identity_file") else None),
                known_hosts=(str(cfg["known_hosts"]).strip() if cfg.get("known_hosts") else None),
                identity_map=tuple(normalized_identity_map),
                connect_timeout_seconds=float(cfg.get("connect_timeout_seconds", 10.0)),
                command_timeout_seconds=float(cfg.get("command_timeout_seconds", 30.0)),
            )
        return result

    @staticmethod
    def _validate_linux_user(user: str) -> str:
        value = str(user or "").strip()
        if not value or len(value) > 64:
            raise SSHRuntimeError("Linux user is required and must be <= 64 characters")
        if any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for ch in value):
            raise SSHRuntimeError("Linux user contains unsupported characters")
        return value

    def _resolve_target(
        self,
        target_name: str,
        identity_id: Optional[str] = None,
    ) -> tuple[SSHTarget, str]:
        target = self._targets.get(str(target_name))
        if target is None:
            raise SSHRuntimeError(f"Unknown SSH target: {target_name}")

        mapped_users = dict(target.identity_map)
        if mapped_users:
            if not identity_id:
                raise SSHRuntimeError("Trusted Alice identity is required for this SSH target")
            mapped = mapped_users.get(str(identity_id))
            if not mapped:
                raise SSHRuntimeError(
                    f"Identity '{identity_id}' has no Linux user mapping on target '{target.name}'"
                )
            user = self._validate_linux_user(mapped)
        elif identity_id:
            user = self._validate_linux_user(target.default_user or "")
        else:
            user = self._validate_linux_user(target.default_user or "")

        if target.allowed_users and user not in target.allowed_users:
            raise SSHRuntimeError(
                f"Linux user '{user}' is not allowed on SSH target '{target.name}'"
            )
        return target, user

    @staticmethod
    def _validate_remote_path(path: str) -> str:
        value = str(path or "").strip()
        if not value or "\x00" in value:
            raise SSHRuntimeError("Remote file path is required")
        if not value.startswith("/"):
            raise SSHRuntimeError("Remote file path must be absolute")
        return value

    def _ssh_command(
        self,
        target: SSHTarget,
        linux_user: str,
        remote_command: str,
    ) -> list[str]:
        known_hosts = target.known_hosts or self._default_known_hosts
        if not known_hosts:
            raise SSHRuntimeError(
                "SSH host-key verification requires configured known_hosts"
            )

        command = [
            "ssh",
            "-p",
            str(target.port),
            "-o",
            "BatchMode=yes",
            "-o",
            f"ConnectTimeout={max(1, int(target.connect_timeout_seconds))}",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            f"UserKnownHostsFile={known_hosts}",
        ]
        if target.identity_file:
            command.extend(["-i", target.identity_file])
        command.extend([f"{linux_user}@{target.host}", "--", remote_command])
        return command

    def execute(
        self,
        *,
        target: str,
        command: str,
        timeout_seconds: Optional[float] = None,
        identity_id: Optional[str] = None,
    ) -> dict[str, Any]:
        command = str(command or "").strip()
        if not command:
            raise SSHRuntimeError("Command is required")

        resolved_target, user = self._resolve_target(target, identity_id)
        timeout = float(timeout_seconds or resolved_target.command_timeout_seconds)
        if timeout <= 0 or timeout > 3600:
            raise SSHRuntimeError("timeout_seconds must be > 0 and <= 3600")

        argv = self._ssh_command(resolved_target, user, command)
        try:
            completed = subprocess.run(
                argv,
                shell=False,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise SSHRuntimeError(
                f"SSH command timed out after {timeout:g}s"
            ) from exc
        except OSError as exc:
            raise SSHRuntimeError(f"Unable to start ssh: {exc}") from exc

        return {
            "success": completed.returncode == 0,
            "target": resolved_target.name,
            "host": resolved_target.host,
            "linux_user": user,
            "command": command,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "exit_code": completed.returncode,
        }

    def read_file(
        self,
        *,
        target: str,
        path: str,
        timeout_seconds: Optional[float] = None,
        identity_id: Optional[str] = None,
    ) -> dict[str, Any]:
        remote_path = self._validate_remote_path(path)
        result = self.execute(
            target=target,
            command=f"cat -- {shlex.quote(remote_path)}",
            timeout_seconds=timeout_seconds,
            identity_id=identity_id,
        )
        result.update({"path": remote_path, "operation": "read_file"})
        return result

    def write_file(
        self,
        *,
        target: str,
        path: str,
        content: str,
        timeout_seconds: Optional[float] = None,
        identity_id: Optional[str] = None,
    ) -> dict[str, Any]:
        remote_path = self._validate_remote_path(path)
        parent = str(PurePosixPath(remote_path).parent)
        quoted_parent = shlex.quote(parent)
        quoted_path = shlex.quote(remote_path)
        template = shlex.quote(parent + "/.alice-runtime-XXXXXX")
        script = (
            "set -eu; "
            f"mkdir -p -- {quoted_parent}; "
            f"tmp=$(mktemp -- {template}); "
            "trap 'rm -f -- \"$tmp\"' EXIT; "
            'cat > "$tmp"; '
            f"mv -f -- \"$tmp\" {quoted_path}; "
            "trap - EXIT"
        )

        resolved_target, user = self._resolve_target(target, identity_id)
        timeout = float(timeout_seconds or resolved_target.command_timeout_seconds)
        if timeout <= 0 or timeout > 3600:
            raise SSHRuntimeError("timeout_seconds must be > 0 and <= 3600")
        argv = self._ssh_command(resolved_target, user, script)

        try:
            completed = subprocess.run(
                argv,
                shell=False,
                check=False,
                input=str(content),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise SSHRuntimeError(
                f"SSH file write timed out after {timeout:g}s"
            ) from exc
        except OSError as exc:
            raise SSHRuntimeError(f"Unable to start ssh: {exc}") from exc

        return {
            "success": completed.returncode == 0,
            "target": resolved_target.name,
            "host": resolved_target.host,
            "linux_user": user,
            "path": remote_path,
            "operation": "write_file",
            "bytes_written": len(str(content).encode("utf-8")),
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "exit_code": completed.returncode,
        }


__all__ = ["SSHRuntime", "SSHRuntimeError", "SSHTarget"]

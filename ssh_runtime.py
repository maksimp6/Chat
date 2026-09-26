"""SSH-backed runtime for executing commands and files as configured Linux users.

The runtime uses the system OpenSSH client. The selected SSH account is the
remote OS identity, so Linux permissions remain the authorization boundary.
"""

from __future__ import annotations

import json
import os
import math
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
    workspace_root: Optional[str] = None
    connect_timeout_seconds: float = 10.0
    command_timeout_seconds: float = 30.0
    max_output_bytes: int = 1048576


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

            try:
                max_output = int(cfg.get("max_output_bytes", 1048576))
            except (TypeError, ValueError) as exc:
                raise SSHRuntimeError(f"SSH target '{name}' has invalid max_output_bytes") from exc
            if not 4096 <= max_output <= 10 * 1024 * 1024:
                raise SSHRuntimeError(f"SSH target '{name}' has invalid max_output_bytes")

            result[str(name)] = SSHTarget(
                name=str(name),
                host=host,
                port=port,
                default_user=(
                    str(cfg["default_user"]).strip() if cfg.get("default_user") else None
                ),
                allowed_users=tuple(str(user).strip() for user in allowed if str(user).strip()),
                identity_file=(
                    str(cfg["identity_file"]).strip() if cfg.get("identity_file") else None
                ),
                known_hosts=(str(cfg["known_hosts"]).strip() if cfg.get("known_hosts") else None),
                identity_map=tuple(normalized_identity_map),
                workspace_root=(
                    str(cfg["workspace_root"]).strip() if cfg.get("workspace_root") else None
                ),
                connect_timeout_seconds=float(cfg.get("connect_timeout_seconds", 10.0)),
                command_timeout_seconds=float(cfg.get("command_timeout_seconds", 30.0)),
                max_output_bytes=int(cfg.get("max_output_bytes", 1048576)),
            )
        return result

    @staticmethod
    def _validate_linux_user(user: str) -> str:
        value = str(user or "").strip()
        if not value or len(value) > 64:
            raise SSHRuntimeError("Linux user is required and must be <= 64 characters")
        if any(
            ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
            for ch in value
        ):
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
    def _limit_output(value: str, max_bytes: int) -> str:
        text = value or ""
        raw = text.encode("utf-8", errors="replace")
        if len(raw) <= max_bytes:
            return text
        suffix = "\n...[output truncated]"
        suffix_bytes = suffix.encode("utf-8")
        budget = max(0, max_bytes - len(suffix_bytes))
        return raw[:budget].decode("utf-8", errors="ignore") + suffix

    @staticmethod
    def _validate_remote_path(path: str) -> str:
        value = str(path or "").strip()
        if not value or "\x00" in value:
            raise SSHRuntimeError("Remote file path is required")
        if not value.startswith("/"):
            raise SSHRuntimeError("Remote file path must be absolute")
        return value

    @staticmethod
    def _path_within_workspace(path: str, workspace_root: Optional[str]) -> str:
        normalized = os.path.normpath(path)
        if not workspace_root:
            return normalized
        root = os.path.normpath(workspace_root)
        if root != "/" and not normalized.startswith(root.rstrip("/") + "/") and normalized != root:
            raise SSHRuntimeError("Remote file path is outside the configured workspace")
        return normalized

    @staticmethod
    def _wrap_remote_command(command: str, timeout: float, home: str) -> str:
        """Run the command with a server-side timeout and minimal environment."""
        seconds = max(1, int(math.ceil(timeout)))
        safe_path = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
        inner = shlex.quote(command)
        return (
            "env -i "
            f"HOME={shlex.quote(home)} "
            f"PATH={shlex.quote(safe_path)} "
            "timeout --foreground --signal=TERM --kill-after=5s "
            f"{seconds}s sh -lc {inner}"
        )

    def _ssh_command(
        self,
        target: SSHTarget,
        linux_user: str,
        remote_command: str,
    ) -> list[str]:
        known_hosts = target.known_hosts or self._default_known_hosts
        if not known_hosts:
            raise SSHRuntimeError("SSH host-key verification requires configured known_hosts")

        command = [
            "ssh",
            "-p",
            str(target.port),
            "-o",
            "BatchMode=yes",
            "-o",
            "IdentitiesOnly=yes",
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

        remote_command = self._wrap_remote_command(
            command,
            timeout,
            home=f"/home/{user}",
        )
        argv = self._ssh_command(resolved_target, user, remote_command)
        try:
            completed = subprocess.run(
                argv,
                shell=False,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout + 10,
            )
        except subprocess.TimeoutExpired as exc:
            raise SSHRuntimeError(f"SSH command timed out after {timeout:g}s") from exc
        except OSError as exc:
            raise SSHRuntimeError(f"Unable to start ssh: {exc}") from exc

        return {
            "success": completed.returncode == 0,
            "target": resolved_target.name,
            "host": resolved_target.host,
            "linux_user": user,
            "command": command,
            "stdout": self._limit_output(completed.stdout, resolved_target.max_output_bytes),
            "stderr": self._limit_output(completed.stderr, resolved_target.max_output_bytes),
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
        target_config = self._targets.get(str(target))
        workspace_root = target_config.workspace_root if target_config else None
        remote_path = self._path_within_workspace(remote_path, workspace_root)
        if workspace_root and os.path.normpath(workspace_root) != "/":
            root = shlex.quote(os.path.normpath(workspace_root))
            path_arg = shlex.quote(remote_path)
            read_command = (
                f"root=$(realpath -e -- {root}) && "
                f"target=$(realpath -e -- {path_arg}) && "
                'case "$target" in "$root"|"$root"/*) cat -- "$target";; '
                '*) echo "Remote file path is outside the configured workspace" >&2; exit 1;; esac'
            )
        else:
            read_command = f"cat -- {shlex.quote(remote_path)}"
        result = self.execute(
            target=target,
            command=read_command,
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
        target_config = self._targets.get(str(target))
        workspace_root = target_config.workspace_root if target_config else None
        remote_path = self._path_within_workspace(remote_path, workspace_root)
        parent = str(PurePosixPath(remote_path).parent)
        quoted_parent = shlex.quote(parent)
        quoted_path = shlex.quote(remote_path)
        template = shlex.quote(parent + "/.alice-runtime-XXXXXX")
        if workspace_root and os.path.normpath(workspace_root) != "/":
            root = shlex.quote(os.path.normpath(workspace_root))
            workspace_guard = (
                f"root=$(realpath -e -- {root}); "
                f"mkdir -p -- {quoted_parent}; "
                f"resolved_parent=$(realpath -e -- {quoted_parent}); "
                'case "$resolved_parent" in "$root"|"$root"/*) ;; '
                '*) echo "Remote file path is outside the configured workspace" >&2; exit 1;; esac; '
            )
        else:
            workspace_guard = f"mkdir -p -- {quoted_parent}; "
        script = (
            "set -eu; "
            f"{workspace_guard}"
            f"tmp=$(mktemp -- {template}); "
            "trap 'rm -f -- \"$tmp\"' EXIT; "
            'cat > "$tmp"; '
            f'mv -f -- "$tmp" {quoted_path}; '
            "trap - EXIT"
        )

        resolved_target, user = self._resolve_target(target, identity_id)
        timeout = float(timeout_seconds or resolved_target.command_timeout_seconds)
        if timeout <= 0 or timeout > 3600:
            raise SSHRuntimeError("timeout_seconds must be > 0 and <= 3600")
        remote_command = self._wrap_remote_command(
            script,
            timeout,
            home=f"/home/{user}",
        )
        argv = self._ssh_command(resolved_target, user, remote_command)

        try:
            completed = subprocess.run(
                argv,
                shell=False,
                check=False,
                input=str(content),
                capture_output=True,
                text=True,
                timeout=timeout + 10,
            )
        except subprocess.TimeoutExpired as exc:
            raise SSHRuntimeError(f"SSH file write timed out after {timeout:g}s") from exc
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
            "stdout": self._limit_output(completed.stdout, resolved_target.max_output_bytes),
            "stderr": self._limit_output(completed.stderr, resolved_target.max_output_bytes),
            "exit_code": completed.returncode,
        }


__all__ = ["SSHRuntime", "SSHRuntimeError", "SSHTarget"]

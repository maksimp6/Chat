"""Docker runtime used by the Alice Cloud control plane."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
import subprocess
from typing import Callable

from cloud.base import CloudProviderError


_MANAGED_LABEL = "alice.cloud.managed"
_SEGMENT = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,62}$")


@dataclass(frozen=True)
class CommandResult:
    """Minimal subprocess result used by the runtime and its tests."""

    returncode: int
    stdout: str = ""
    stderr: str = ""


Runner = Callable[[list[str]], CommandResult]


def _default_runner(argv: list[str]) -> CommandResult:
    completed = subprocess.run(
        argv,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


def _segment(value: str, field: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _SEGMENT.fullmatch(normalized):
        raise CloudProviderError(
            f"Invalid {field}",
            code="invalid_resource_name",
        )
    return normalized


class DockerRuntime:
    """Own and reconcile Alice-managed Docker containers on one node."""

    def __init__(self, *, binary: str = "docker", runner: Runner | None = None) -> None:
        self.binary = binary
        self._runner = runner or _default_runner

    def _run(self, *args: str) -> str:
        result = self._runner([self.binary, *args])
        if result.returncode != 0:
            raise CloudProviderError(
                "Container runtime command failed",
                code="runtime_command_failed",
            )
        return result.stdout

    @staticmethod
    def container_name(lane: str, service: str) -> str:
        return f"alice-{_segment(lane, 'lane')}-{_segment(service, 'service')}"

    def _labels(self, lane: str, service: str) -> list[str]:
        return [
            "--label",
            f"{_MANAGED_LABEL}=1",
            "--label",
            f"alice.cloud.lane={_segment(lane, 'lane')}",
            "--label",
            f"alice.cloud.service={_segment(service, 'service')}",
        ]

    def _ensure_owned(self, name: str) -> dict[str, str]:
        output = self._run("inspect", "--format", "{{json .Config.Labels}}", name).strip()
        try:
            labels = json.loads(output)
        except json.JSONDecodeError as exc:
            raise CloudProviderError("Invalid runtime response", code="invalid_runtime_response") from exc
        if not isinstance(labels, dict) or labels.get(_MANAGED_LABEL) != "1":
            raise CloudProviderError("Resource is not owned by Alice Cloud", code="resource_not_owned")
        return {str(key): str(value) for key, value in labels.items()}

    def list_managed(self) -> list[dict[str, str]]:
        output = self._run(
            "ps",
            "-a",
            "--filter",
            f"label={_MANAGED_LABEL}=1",
            "--format",
            "{{json .}}",
        )
        resources: list[dict[str, str]] = []
        for line in output.splitlines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise CloudProviderError(
                    "Invalid runtime response",
                    code="invalid_runtime_response",
                ) from exc
            labels_text = str(item.get("Labels") or "")
            labels = {}
            for pair in labels_text.split(","):
                key, sep, value = pair.partition("=")
                if sep:
                    labels[key] = value
            resources.append(
                {
                    "id": str(item.get("ID") or ""),
                    "name": str(item.get("Names") or ""),
                    "image": str(item.get("Image") or ""),
                    "state": str(item.get("State") or ""),
                    "status": str(item.get("Status") or ""),
                    "lane": labels.get("alice.cloud.lane", ""),
                    "service": labels.get("alice.cloud.service", ""),
                }
            )
        return resources

    def create(self, *, lane: str, service: str, config: dict) -> dict[str, str]:
        image = str(config.get("image") or "").strip()
        if not image:
            raise CloudProviderError("Container image is required", code="image_required")
        scale = int(config.get("scale", 1))
        if scale not in {0, 1}:
            raise CloudProviderError("Alice Cloud MVP supports scale 0 or 1", code="invalid_scale")
        name = self.container_name(lane, service)
        self._run(
            "create",
            "--name",
            name,
            *self._labels(lane, service),
            image,
        )
        if scale == 1:
            self._run("start", name)
        return {"name": name, "lane": lane, "service": service, "state": "running" if scale else "stopped"}

    def update(self, *, lane: str, service: str, config: dict) -> dict[str, str]:
        name = self.container_name(lane, service)
        self._ensure_owned(name)
        scale = int(config.get("scale", 1))
        if scale not in {0, 1}:
            raise CloudProviderError("Alice Cloud MVP supports scale 0 or 1", code="invalid_scale")
        self._run("start" if scale else "stop", name)
        return {"name": name, "lane": lane, "service": service, "state": "running" if scale else "stopped"}

    def delete(self, *, lane: str, service: str) -> dict[str, str]:
        name = self.container_name(lane, service)
        self._ensure_owned(name)
        self._run("rm", "-f", name)
        return {"name": name, "lane": lane, "service": service, "state": "deleted"}

    def lifecycle(self, *, operation: str, name: str) -> dict[str, str]:
        if operation not in {"start", "stop", "restart"}:
            raise CloudProviderError("Unsupported compute operation", code="unsupported_operation")
        self._ensure_owned(name)
        self._run(operation, name)
        return {"name": name, "operation": operation, "status": "accepted"}

    def logs(self, *, name: str, limit: int) -> str:
        self._ensure_owned(name)
        bounded = max(1, min(int(limit), 1000))
        return self._run("logs", "--tail", str(bounded), name)

    def stats(self, *, name: str) -> dict:
        self._ensure_owned(name)
        output = self._run("stats", "--no-stream", "--format", "{{json .}}", name).strip()
        try:
            value = json.loads(output)
        except json.JSONDecodeError as exc:
            raise CloudProviderError("Invalid runtime response", code="invalid_runtime_response") from exc
        if not isinstance(value, dict):
            raise CloudProviderError("Invalid runtime response", code="invalid_runtime_response")
        return value

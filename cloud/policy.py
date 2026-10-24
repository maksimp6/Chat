"""Cloud operation policy helpers."""

from __future__ import annotations

DANGEROUS_COMPUTE_OPERATIONS = {"start", "stop", "reboot"}
DANGEROUS_BACKUP_OPERATIONS = {"create", "restore", "delete"}


READ_ONLY_TOOLS = {
    "cloud.capabilities",
    "cloud.resources.list",
    "cloud.resources.get",
    "cloud.compute.list",
    "cloud.compute.status",
    "cloud.logs.query",
    "cloud.metrics.query",
    "cloud.costs.summary",
    "cloud.budget.status",
}


CONFIRMATION_REQUIRED_TOOLS = {
    "cloud.compute.start",
    "cloud.compute.stop",
    "cloud.compute.reboot",
    "cloud.backup.create",
    "cloud.ssh.exec",
}


def tool_requires_confirmation(name: str) -> bool:
    return name in CONFIRMATION_REQUIRED_TOOLS

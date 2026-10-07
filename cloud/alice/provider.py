"""Alice Cloud: self-hosted control plane for Alice Pro infrastructure."""

from __future__ import annotations

from typing import Any

from cloud.base import CloudProviderError
from cloud.alice.runtime import DockerRuntime


class AliceCloudProvider:
    """Provider implementation backed by Alice-owned compute nodes."""

    name = "alice"

    def __init__(self, runtime: DockerRuntime | None = None) -> None:
        self.runtime = runtime or DockerRuntime()

    def capabilities(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "control_plane": "alice",
            "runtime": "docker",
            "containers": True,
            "scale": {"min": 0, "max": 1},
            "logs": True,
            "metrics": True,
            "backups": False,
            "external_provider_required": False,
        }

    def list_resources(
        self,
        *,
        service: str,
        resource_type: str | None = None,
        filters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if resource_type not in {None, "container", "containers"}:
            raise CloudProviderError("Unsupported resource type", code="unsupported_resource_type")
        items = self.runtime.list_managed()
        if service and service != "*":
            items = [item for item in items if item.get("service") == service]
        if filters:
            for key, value in filters.items():
                items = [item for item in items if item.get(str(key)) == str(value)]
        return {"provider": self.name, "containers": items}

    def get_resource(
        self,
        *,
        resource_type: str,
        resource_id: str,
        service: str | None = None,
    ) -> dict[str, Any]:
        resources = self.list_resources(service=service or "*", resource_type=resource_type)["containers"]
        for item in resources:
            if item.get("id") == resource_id or item.get("name") == resource_id:
                return item
        raise CloudProviderError("Resource not found", code="resource_not_found")

    def reconcile_container(
        self,
        *,
        operation: str,
        lane: str,
        service: str,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        if operation == "create":
            return self.runtime.create(lane=lane, service=service, config=config)
        if operation == "update":
            return self.runtime.update(lane=lane, service=service, config=config)
        if operation == "delete":
            return self.runtime.delete(lane=lane, service=service)
        raise CloudProviderError("Unsupported reconciliation operation", code="unsupported_operation")

    def compute(
        self,
        *,
        operation: str,
        instance_id: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not instance_id:
            raise CloudProviderError("instance_id is required", code="resource_id_required")
        return self.runtime.lifecycle(operation=operation, name=instance_id)

    def query_logs(
        self,
        *,
        query: str,
        service: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        if not service:
            raise CloudProviderError("service is required", code="service_required")
        resources = self.list_resources(service=service)["containers"]
        if len(resources) != 1:
            raise CloudProviderError("Service must resolve to one container", code="ambiguous_service")
        text = self.runtime.logs(name=resources[0]["name"], limit=limit)
        if query:
            lines = [line for line in text.splitlines() if query.lower() in line.lower()]
        else:
            lines = text.splitlines()
        return {"provider": self.name, "service": service, "lines": lines[-limit:]}

    def query_metrics(
        self,
        *,
        query: str,
        service: str | None = None,
        window: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        if not service:
            raise CloudProviderError("service is required", code="service_required")
        resources = self.list_resources(service=service)["containers"]
        if len(resources) != 1:
            raise CloudProviderError("Service must resolve to one container", code="ambiguous_service")
        return {
            "provider": self.name,
            "service": service,
            "sample": self.runtime.stats(name=resources[0]["name"]),
            "window": window,
        }

    def backup(
        self,
        *,
        operation: str,
        resource_id: str | None = None,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        raise CloudProviderError("Backups are not implemented in Alice Cloud MVP", code="not_implemented")

    def costs_summary(
        self,
        *,
        period: str | None = None,
        group_by: str | None = None,
    ) -> dict[str, Any]:
        return {
            "provider": self.name,
            "billing_model": "self_hosted",
            "provider_charge": 0.0,
            "currency": "RUB",
            "period": period,
            "group_by": group_by,
            "note": "Host, electricity and network costs are measured separately.",
        }

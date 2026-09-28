"""Provider-neutral cloud abstractions for Alice Pro tooling."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class CloudProviderError(RuntimeError):
    message: str
    code: str = "cloud_error"
    http_status: int | None = None

    def __str__(self) -> str:
        suffix = f" (HTTP {self.http_status})" if self.http_status else ""
        return f"{self.code}: {self.message}{suffix}"


class CloudProvider(Protocol):
    name: str

    def capabilities(self) -> dict[str, Any]: ...

    def list_resources(
        self,
        *,
        service: str,
        resource_type: str | None = None,
        filters: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    def get_resource(
        self, *, resource_type: str, resource_id: str, service: str | None = None
    ) -> dict[str, Any]: ...

    def compute(
        self, *, operation: str, instance_id: str | None = None, extra: dict[str, Any] | None = None
    ) -> dict[str, Any]: ...

    def query_logs(
        self, *, query: str, service: str | None = None, limit: int = 100
    ) -> dict[str, Any]: ...

    def query_metrics(
        self, *, query: str, service: str | None = None, window: str | None = None, limit: int = 100
    ) -> dict[str, Any]: ...

    def backup(
        self,
        *,
        operation: str,
        resource_id: str | None = None,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    def costs_summary(
        self, *, period: str | None = None, group_by: str | None = None
    ) -> dict[str, Any]: ...

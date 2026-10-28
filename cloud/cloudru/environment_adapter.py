"""Cloud.ru-backed `EnvironmentAdapter`: sandboxes via Container Apps Jobs / Compute VM.

Replaces local subprocess execution and same-VM SSH containers with an
isolated remote resource. Lifecycle calls are plain Cloud.ru REST requests
through `cloud/cloudru/provider.py`; this module only adapts the
provider-neutral environment shape (`environment_manager.py` records) to the
`cloud.environment.EnvironmentAdapter` contract.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from cloud.base import CloudProviderError
from cloud.cloudru.provider import CloudRuProvider
from cloud.registry import ensure_default_providers


class CloudRuEnvironmentAdapter:
    """Adapter for environment records with `adapter == "cloudru"`."""

    name = "cloudru"

    def __init__(self, environment: Dict[str, Any], *, provider: Optional[CloudRuProvider] = None):
        self.environment = environment
        self._provider = provider

    def _provider_instance(self) -> CloudRuProvider:
        if self._provider is not None:
            return self._provider
        return ensure_default_providers().get("cloudru")

    def _remote_id(self) -> str:
        remote_id = self.environment.get("cloud_resource_id")
        if not remote_id:
            raise CloudProviderError(
                "Cloud.ru environment has not been provisioned", code="not_provisioned"
            )
        return str(remote_id)

    def provision(self) -> Dict[str, Any]:
        provider = self._provider_instance()
        result = provider.environment_create(
            name=self.environment["environment_id"],
            commit_sha=self.environment["commit_sha"],
            branch=self.environment.get("branch_name"),
            ttl_seconds=self.environment.get("ttl_seconds"),
        )
        resource = result.get("resource") if isinstance(result.get("resource"), dict) else {}
        remote_id = resource.get("id")
        if not remote_id:
            raise CloudProviderError(
                "Cloud.ru did not return an environment id", code="invalid_response"
            )
        return {"cloud_resource_id": str(remote_id)}

    def start(self) -> Dict[str, Any]:
        self._provider_instance().environment_start(remote_id=self._remote_id())
        return {}

    def execute(self, command: str, *, timeout_seconds: float = 30.0) -> Dict[str, Any]:
        result = self._provider_instance().environment_exec(
            remote_id=self._remote_id(), command=command, timeout_seconds=timeout_seconds
        )
        payload = result.get("result")
        payload = payload if isinstance(payload, dict) else {}
        exit_code = payload.get("exit_code")
        return {
            "success": bool(payload.get("success", exit_code == 0)),
            "stdout": payload.get("stdout", ""),
            "stderr": payload.get("stderr", ""),
            "exit_code": exit_code,
        }

    def stop(self) -> None:
        if not self.environment.get("cloud_resource_id"):
            return
        self._provider_instance().environment_stop(remote_id=self._remote_id())

    def remove(self) -> None:
        if not self.environment.get("cloud_resource_id"):
            return
        self._provider_instance().environment_delete(remote_id=self._remote_id())


__all__ = ["CloudRuEnvironmentAdapter"]

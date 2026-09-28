"""Cloud.ru Evolution Container Apps client (serverless containers, no VMs).

Evolution has no separate Functions product that runs a Docker image; Container
Apps is the serverless runtime that pulls images from Artifact Registry.

Endpoints follow the Container Apps public API (``https://containers.api.cloud.ru``):
https://cloud.ru/docs/container-apps-evolution/ug/topics/api-ref . The current
Container Services API uses the v2 resource paths; the v1 detail path returns a
non-standard error on the live service.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
import os
import re
import time
from typing import Any, Callable

import requests

from cloud.base import CloudProviderError
from cloud.cloudru.client import CloudRuClient
from cloudru_iam import CloudRuIamClient


SERVICE = "container_apps"
_NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
_READY_STATUSES = {"RUNNING", "READY", "ACTIVE", "DEPLOYED"}
_FAILED_MARKERS = ("FAIL", "ERROR", "CRASH")

# Allowed vCPU -> memory pairs for a container service.
CPU_MEMORY = {"0.1": "256Mi", "0.2": "512Mi", "0.3": "768Mi", "0.5": "1024Mi", "1": "4096Mi"}

# Container Services pay-as-you-go prices from the Cloud.ru pricing example (RUB).
# https://cloud.ru/docs/container-apps-evolution/ug/topics/pricing__container-services
PRICE_VCPU_HOUR = 1.8905
PRICE_GB_HOUR = 1.2570
FREE_VCPU_HOURS = 25.0
FREE_GB_HOURS = 50.0
HOURS_PER_MONTH = 730.0


@dataclass
class ContainerSpec:
    name: str
    image: str
    port: int = 8080
    cpu: str = "0.5"
    min_instances: int = 0
    max_instances: int = 1
    public: bool = True
    env: dict[str, str] = field(default_factory=dict)
    protocol: str = "http_1"
    timeout: str = "300s"
    idle_timeout: str = "600s"
    description: str = "Alice Pro"

    @property
    def memory(self) -> str:
        return CPU_MEMORY[self.cpu]

    def validate(self) -> None:
        if not _NAME_RE.match(self.name or ""):
            raise CloudProviderError(
                "name must be lowercase letters, digits and dashes", code="validation_error"
            )
        if not self.image:
            raise CloudProviderError("image is required", code="validation_error")
        if self.cpu not in CPU_MEMORY:
            raise CloudProviderError(
                f"cpu must be one of {sorted(CPU_MEMORY)}", code="validation_error"
            )
        if not 0 <= self.min_instances <= self.max_instances:
            raise CloudProviderError(
                "min_instances must be between 0 and max_instances", code="validation_error"
            )

    def container_body(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "image": self.image,
            "containerPort": int(self.port),
            "resources": {"cpu": self.cpu, "memory": self.memory},
            "env": [{"name": k, "value": v} for k, v in sorted(self.env.items())],
        }


def estimate_monthly_cost(
    cpu: str, min_instances: int, *, hours: float = HOURS_PER_MONTH
) -> dict[str, float]:
    """Floor cost in RUB of keeping ``min_instances`` warm for a month, after the free tier.

    Traffic that scales above ``min_instances`` adds to this.
    """
    vcpu_hours = float(cpu) * min_instances * hours
    gb_hours = int(CPU_MEMORY[cpu].removesuffix("Mi")) / 1024 * min_instances * hours
    vcpu_cost = max(vcpu_hours - FREE_VCPU_HOURS, 0.0) * PRICE_VCPU_HOUR
    ram_cost = max(gb_hours - FREE_GB_HOURS, 0.0) * PRICE_GB_HOUR
    return {
        "vcpu_hours": round(vcpu_hours, 1),
        "gb_hours": round(gb_hours, 1),
        "rub_per_month": round(vcpu_cost + ram_cost, 2),
    }


_READONLY_FIELDS = ("status", "id", "createdAt", "updatedAt")


def _writable(app: dict[str, Any]) -> dict[str, Any]:
    body = copy.deepcopy(app)
    for readonly in _READONLY_FIELDS:
        body.pop(readonly, None)
    return body


def _image_of(app: dict[str, Any]) -> str | None:
    containers = (app.get("template") or {}).get("containers") or [{}]
    return containers[0].get("image")


class CloudRuContainerAppsClient:
    def __init__(
        self,
        *,
        project_id: str | None = None,
        client: CloudRuClient | None = None,
        iam_client: CloudRuIamClient | None = None,
        sleep: Callable[[float], None] = time.sleep,
        http_get: Callable[..., Any] = requests.get,
    ) -> None:
        self.client = client or CloudRuClient(
            iam_client=iam_client or CloudRuIamClient(), api_key_auth=False
        )
        self.project_id = (project_id or os.getenv("CLOUDRU_PROJECT_ID", "")).strip()
        self._sleep = sleep
        self._http_get = http_get

    def _project(self) -> str:
        if not self.project_id:
            raise CloudProviderError("CLOUDRU_PROJECT_ID is required", code="validation_error")
        return self.project_id

    @staticmethod
    def _name(name: str) -> str:
        if not _NAME_RE.match(name or ""):
            raise CloudProviderError(
                "name must be lowercase letters, digits and dashes", code="validation_error"
            )
        return name

    # Read -------------------------------------------------------------------

    def get(self, name: str) -> dict[str, Any] | None:
        try:
            return self.client.request(
                SERVICE,
                "GET",
                f"/v2/containers/{self._name(name)}",
                params={"projectId": self._project()},
            )
        except CloudProviderError as exc:
            if exc.http_status == 404:
                return None
            raise

    def status(self, name: str) -> dict[str, Any]:
        """Condensed, secret-free view: status, public URL, image, scaling."""
        app = self.get(name)
        if app is None:
            return {"name": name, "exists": False, "status": "NOT_FOUND"}
        ingress = (app.get("configuration") or {}).get("ingress") or {}
        template = app.get("template") or {}
        containers = template.get("containers") or [{}]
        return {
            "name": name,
            "exists": True,
            "status": str(app.get("status") or "UNKNOWN"),
            "public_uri": ingress.get("publicUri"),
            "image": containers[0].get("image"),
            "resources": containers[0].get("resources"),
            "scaling": template.get("scaling"),
        }

    # Write ------------------------------------------------------------------

    def create(self, spec: ContainerSpec) -> dict[str, Any]:
        spec.validate()
        body = {
            "name": spec.name,
            "projectId": self._project(),
            "description": spec.description,
            "configuration": {
                "ingress": {"publiclyAccessible": spec.public},
                "autoDeployments": {"enabled": False},
            },
            "template": {
                "timeout": spec.timeout,
                "idleTimeout": spec.idle_timeout,
                "protocol": spec.protocol,
                "scaling": {
                    "minInstanceCount": spec.min_instances,
                    "maxInstanceCount": spec.max_instances,
                },
                "containers": [spec.container_body()],
            },
        }
        return self.client.request(SERVICE, "POST", "/v2/containers", json_body=body)

    def update(self, spec: ContainerSpec) -> dict[str, Any]:
        """Roll out a new revision with ``spec`` applied to the current configuration."""
        spec.validate()
        current = self.get(spec.name)
        if current is None:
            raise CloudProviderError(f"container '{spec.name}' not found", code="not_found")
        body = _writable(current)
        body.setdefault("configuration", {}).setdefault("ingress", {})["publiclyAccessible"] = (
            spec.public
        )
        template = body.setdefault("template", {})
        template["scaling"] = {
            **(template.get("scaling") or {}),
            "minInstanceCount": spec.min_instances,
            "maxInstanceCount": spec.max_instances,
        }
        containers = template.get("containers") or [{}]
        containers[0] = {**containers[0], **spec.container_body()}
        template["containers"] = containers
        return self.client.request(
            SERVICE,
            "PATCH",
            f"/v2/containers/{spec.name}",
            params={"projectId": self._project()},
            json_body=body,
        )

    def restore(self, name: str, previous: dict[str, Any]) -> dict[str, Any]:
        """Roll out a revision with exactly the configuration captured in ``previous``."""
        return self.client.request(
            SERVICE,
            "PATCH",
            f"/v2/containers/{self._name(name)}",
            params={"projectId": self._project()},
            json_body=_writable(previous),
        )

    def deploy(self, spec: ContainerSpec) -> dict[str, Any]:
        """Create the container service, or roll out a new revision if it exists."""
        if self.get(spec.name) is None:
            return {"action": "create", "operation": self.create(spec)}
        return {"action": "update", "operation": self.update(spec)}

    def deploy_verified(self, spec: ContainerSpec, *, timeout_s: float = 600) -> dict[str, Any]:
        """Deploy, wait for the new image, health-check, and restore the previous revision on failure.

        The rollback restores the whole previous configuration (image, env, scaling,
        resources, ingress), not just the image.
        """
        spec.validate()
        previous = self.get(spec.name)
        previous_image = _image_of(previous) if previous else None
        result = self.deploy(spec)
        try:
            ready = self.wait_until_ready(spec.name, image=spec.image, timeout_s=timeout_s)
            health = self.health_check(ready["public_uri"])
        except CloudProviderError as exc:
            if previous is None:
                raise
            self.restore(spec.name, previous)
            self.wait_until_ready(spec.name, image=previous_image, timeout_s=timeout_s)
            raise CloudProviderError(
                f"{exc.message}; rolled back to {previous_image}",
                code=exc.code,
                http_status=exc.http_status,
            ) from exc
        return {
            "action": result["action"],
            "previous_image": previous_image,
            "status": ready,
            "health": health,
        }

    def delete(self, name: str) -> dict[str, Any]:
        return self.client.request(
            SERVICE,
            "DELETE",
            f"/v2/containers/{self._name(name)}",
            params={"projectId": self._project()},
        )

    def start(self, name: str) -> dict[str, Any]:
        return self.client.request(
            SERVICE,
            "POST",
            f"/v2/containers/{self._name(name)}:start",
            params={"projectId": self._project()},
        )

    def stop(self, name: str) -> dict[str, Any]:
        return self.client.request(
            SERVICE,
            "POST",
            f"/v2/containers/{self._name(name)}:stop",
            params={"projectId": self._project()},
        )

    # Verification -----------------------------------------------------------

    def wait_until_ready(
        self, name: str, *, image: str | None = None, timeout_s: float = 600, poll_s: float = 10
    ) -> dict[str, Any]:
        """Poll until the service is running (on ``image`` if given) and has a public URL."""
        deadline = time.monotonic() + timeout_s
        last: dict[str, Any] = {}
        while True:
            last = self.status(name)
            state = last["status"].upper()
            if any(marker in state for marker in _FAILED_MARKERS):
                raise CloudProviderError(f"container '{name}' is {state}", code="deploy_failed")
            on_image = image is None or last.get("image") == image
            if state in _READY_STATUSES and last.get("public_uri") and on_image:
                return last
            if time.monotonic() >= deadline:
                raise CloudProviderError(
                    f"container '{name}' not ready after {timeout_s:.0f}s (status {state})",
                    code="deploy_timeout",
                )
            self._sleep(poll_s)

    def health_check(
        self, public_uri: str, *, path: str = "/healthz", attempts: int = 12, delay_s: float = 5
    ) -> dict[str, Any]:
        base = public_uri if public_uri.startswith("http") else f"https://{public_uri}"
        url = f"{base.rstrip('/')}{path}"
        last_error = ""
        for attempt in range(1, attempts + 1):
            try:
                response = self._http_get(url, timeout=10)
                if response.status_code == 200:
                    return {"url": url, "http_status": 200, "attempts": attempt}
                last_error = f"HTTP {response.status_code}"
            except requests.RequestException as exc:
                last_error = type(exc).__name__
            if attempt < attempts:
                self._sleep(delay_s)
        raise CloudProviderError(
            f"health check {url} failed: {last_error}", code="health_check_failed"
        )

"""Read-only Cloud.ru observed-state provider.

The provider deliberately exposes only the same allow-listed fields as
scripts.cloudru_check. Secret values and raw provider errors never cross this boundary.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Dict, Mapping

from cloud.cloudru.container_apps_client import CloudRuContainerAppsClient
from scripts.cloudru_check import containers as safe_containers


def get_observed_state(
    lane: str,
    *,
    env: Mapping[str, str] | None = None,
    client_factory: Callable[[str], Any] | None = None,
) -> Dict[str, Any]:
    """Return redacted Container Apps state for a platform lane."""
    if lane not in {"test", "production"}:
        raise ValueError(f"unknown lane: {lane}")
    values = os.environ if env is None else env
    project_id = values.get("CLOUDRU_PROJECT_ID")
    if not project_id:
        return {"containers": [], "provider": "cloudru", "configured": False}
    factory = client_factory or (lambda project: CloudRuContainerAppsClient(project_id=project))
    client = factory(project_id)
    return {
        "containers": safe_containers(client),
        "provider": "cloudru",
        "configured": True,
    }

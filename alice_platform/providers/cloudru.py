"""Cloud.ru provider - read and manage Cloud.ru container apps."""

from typing import Any, Dict, Optional
import logging

from cloud.cloudru.container_apps_client import (
    CloudRuContainerAppsClient,
    ContainerSpec,
)

logger = logging.getLogger(__name__)

# Container name prefix for Alice services
CONTAINER_PREFIX = "alice-"


def _container_name(lane: str, service_name: str) -> str:
    """Generate Cloud.ru container name from lane and service."""
    safe_service = service_name.replace("_", "-").lower()
    return f"{CONTAINER_PREFIX}{lane}-{safe_service}"


def _get_image(service_name: str) -> str:
    """Get Docker image URI for service."""
    # In production, this would lookup from artifact registry
    # For now, use a standard pattern
    return f"artifact-registry.cloud.ru/alice/{service_name}:latest"


def get_observed_state(lane: str) -> Dict[str, Any]:
    """
    Get observed state from Cloud.ru.

    Args:
        lane: Lane name (test, production, etc)

    Returns:
        Observed state dict with containers, storage, etc
    """
    try:
        client = CloudRuContainerAppsClient()
        filter_expr = f'name.startsWith("{CONTAINER_PREFIX}{lane}")'
        apps = client.list(filter_expr=filter_expr)

        containers = []
        for app in apps:
            template = app.get("template") or {}
            containers_list = template.get("containers") or [{}]
            container = containers_list[0]
            resources = container.get("resources") or {}

            # Parse service name from container name
            name = app.get("name", "")
            prefix = f"{CONTAINER_PREFIX}{lane}-"
            if name.startswith(prefix):
                service = name[len(prefix) :]

                containers.append(
                    {
                        "name": name,
                        "service": service,
                        "lane": lane,
                        "status": app.get("status"),
                        "image": container.get("image"),
                        "cpu": resources.get("cpu"),
                        "memory": resources.get("memory"),
                        "scaling": template.get("scaling"),
                    }
                )

        logger.info(f"Observed {len(containers)} containers in {lane}")
        return {"containers": containers}
    except Exception as e:
        logger.warning(f"Failed to fetch observed state from Cloud.ru: {e}")
        return {"containers": []}


def create_container(lane: str, service_name: str, config: Dict[str, Any]) -> None:
    """
    Create a new container in Cloud.ru.

    Args:
        lane: Lane name (test, production, etc)
        service_name: Service name to create
        config: Service configuration with resources, etc
    """
    resources = config.get("resources", {})
    cpu = resources.get("cpu", "0.5")
    gpu = resources.get("gpu")
    name = _container_name(lane, service_name)
    image = _get_image(service_name)

    # GPU enforcement: enforce on production
    if gpu and lane == "production":
        logger.info(f"Creating container {service_name} with GPU={gpu} enforcement")

    try:
        spec = ContainerSpec(
            name=name,
            image=image,
            cpu=cpu,
            max_instances=int(resources.get("scale", 1)),
        )
        client = CloudRuContainerAppsClient()
        result = client.create(spec)
        logger.info(f"[CREATE] {service_name}: {result.get('name')} in {lane}")
    except Exception as e:
        logger.warning(f"[DRY-RUN] Would create {name}: {e}")


def update_container(lane: str, service_name: str, config: Dict[str, Any]) -> None:
    """
    Update an existing container in Cloud.ru.

    Args:
        lane: Lane name (test, production, etc)
        service_name: Service name to update
        config: Service configuration with updated resources, etc
    """
    resources = config.get("resources", {})
    cpu = resources.get("cpu", "0.5")
    gpu = resources.get("gpu")
    name = _container_name(lane, service_name)
    image = _get_image(service_name)

    # GPU enforcement: enforce on production
    if gpu and lane == "production":
        logger.info(f"Updating container {service_name} with GPU={gpu} enforcement")

    try:
        spec = ContainerSpec(
            name=name,
            image=image,
            cpu=cpu,
            max_instances=int(resources.get("scale", 1)),
        )
        client = CloudRuContainerAppsClient()
        result = client.update(spec)
        logger.info(f"[UPDATE] {service_name}: {result.get('name')} in {lane}")
    except Exception as e:
        logger.warning(f"[DRY-RUN] Would update {name}: {e}")


def delete_container(lane: str, service_name: str) -> None:
    """
    Delete a container from Cloud.ru.

    Args:
        lane: Lane name (test, production, etc)
        service_name: Service name to delete
    """
    name = _container_name(lane, service_name)

    try:
        client = CloudRuContainerAppsClient()
        client.delete(name)
        logger.info(f"[DELETE] {service_name}: {name} from {lane}")
    except Exception as e:
        logger.warning(f"[DRY-RUN] Would delete {name}: {e}")

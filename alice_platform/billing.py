"""Billing and revenue tracking for Alice services."""

from dataclasses import dataclass
from typing import Any, Dict, Optional
import logging

from cloud.cloudru.container_apps_client import estimate_monthly_cost

logger = logging.getLogger(__name__)


@dataclass
class BillingInfo:
    """Billing information for a service."""

    service: str
    lane: str
    cpu: str
    memory: str
    min_instances: int
    base_cost_rub: float
    gpu_cost_rub: Optional[float] = None
    total_cost_rub: Optional[float] = None


def calculate_service_cost(
    service_name: str,
    service_config: Dict[str, Any],
    lane: str,
) -> BillingInfo:
    """
    Calculate monthly cost for a service.

    Args:
        service_name: Service name
        service_config: Service configuration
        lane: Lane name

    Returns:
        Billing information with estimated costs
    """
    resources = service_config.get("resources", {})
    cpu = resources.get("cpu", "0.5")
    memory = resources.get("memory", "512Mi")
    min_instances = int(resources.get("scale", 1))
    gpu = resources.get("gpu")
    gpu_memory = resources.get("gpu_memory")

    # Calculate base cost
    cost_info = estimate_monthly_cost(cpu, min_instances)
    base_cost_rub = cost_info.get("rub_per_month", 0.0)

    # Calculate GPU cost if present
    gpu_cost_rub = None
    if gpu and gpu_memory:
        # Rough estimate: 1 GPU ≈ 15 RUB/month (placeholder)
        gpu_cost_rub = float(gpu) * 15.0

    total_cost_rub = base_cost_rub
    if gpu_cost_rub:
        total_cost_rub += gpu_cost_rub

    logger.info(
        f"{service_name} ({lane}): CPU={cpu}, instances={min_instances}, "
        f"base={base_cost_rub:.2f} RUB/month, total={total_cost_rub:.2f} RUB/month"
    )

    return BillingInfo(
        service=service_name,
        lane=lane,
        cpu=cpu,
        memory=memory,
        min_instances=min_instances,
        base_cost_rub=base_cost_rub,
        gpu_cost_rub=gpu_cost_rub,
        total_cost_rub=total_cost_rub,
    )


def calculate_lane_cost(lane: str, config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Calculate total cost for a lane.

    Args:
        lane: Lane name
        config: Configuration dict

    Returns:
        Summary with total cost, services, and breakdown
    """
    lanes = config.get("lanes", {})
    lane_config = lanes.get(lane, {})
    services = config.get("services", {})

    lane_services = lane_config.get("services", [])
    billing_list = []
    total_cost = 0.0

    for service_name in lane_services:
        service_config = services.get(service_name, {})
        if not service_config:
            continue

        billing = calculate_service_cost(service_name, service_config, lane)
        billing_list.append(billing)
        total_cost += billing.total_cost_rub

    logger.info(f"{lane} lane total: {total_cost:.2f} RUB/month")

    return {
        "lane": lane,
        "total_rub_per_month": round(total_cost, 2),
        "services": len(billing_list),
        "breakdown": billing_list,
    }

"""Health checks derived from config."""

from dataclasses import dataclass
from typing import Any, Dict, List


@dataclass
class HealthCheck:
    """A health check derived from config."""

    service: str
    endpoint: str
    expected_sign_in: str
    lane: str
    protocol: str = "https"


def generate_health_plan(
    lane: str,
    config: Dict[str, Any],
) -> List[HealthCheck]:
    """
    Generate health checks from config for a specific lane.

    Health checks are derived from:
    - Domains configured for services in this lane
    - Sign_in method for the lane

    Args:
        lane: Lane name (test, production, etc)
        config: Config dict with services, domains, lanes

    Returns:
        List of health checks to monitor
    """
    checks: List[HealthCheck] = []

    # Get lane config
    lanes = config.get("lanes", {})
    lane_config = lanes.get(lane, {})

    if not lane_config:
        return []

    # Get services deployed in this lane
    lane_services = set(lane_config.get("services", []))
    expected_sign_in = lane_config.get("sign_in", "passphrase")

    # Get domains
    domains = config.get("domains", {})

    # Generate checks for each domain serving a service in this lane
    for domain, domain_config in domains.items():
        if not isinstance(domain_config, dict):
            continue

        service = domain_config.get("service")
        if not service or service not in lane_services:
            continue

        protocol = domain_config.get("protocol", "https")
        endpoint = f"{protocol}://{domain}"

        check = HealthCheck(
            service=service,
            endpoint=endpoint,
            expected_sign_in=expected_sign_in,
            lane=lane,
            protocol=protocol,
        )
        checks.append(check)

    return checks

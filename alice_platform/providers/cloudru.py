"""Cloud.ru provider boundary.

The platform must not treat an unavailable provider as an empty cloud or a successful
"dry run". Until a real read-only observed-state adapter and an explicitly approved
mutation adapter are wired, every provider operation fails closed.
"""

from typing import Any, Dict


class CloudProviderUnavailable(RuntimeError):
    """Cloud.ru provider is not wired to authoritative observed/apply APIs."""


def _unavailable(operation: str) -> None:
    raise CloudProviderUnavailable(
        f"Cloud.ru provider unavailable for {operation}; no authoritative provider is configured"
    )


def get_observed_state(lane: str) -> Dict[str, Any]:
    """Return authoritative observed state, or fail closed when unavailable."""
    _unavailable(f"observed-state read for lane {lane}")


def create_container(lane: str, service_name: str, config: Dict[str, Any]) -> None:
    """Create a container only through a real approved provider."""
    _unavailable(f"create {service_name} in lane {lane}")


def update_container(lane: str, service_name: str, config: Dict[str, Any]) -> None:
    """Update a container only through a real approved provider."""
    _unavailable(f"update {service_name} in lane {lane}")


def delete_container(lane: str, service_name: str) -> None:
    """Delete a container only through a real approved provider."""
    _unavailable(f"delete {service_name} in lane {lane}")

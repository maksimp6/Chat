"""Runtime registry for Alice infrastructure providers."""

from __future__ import annotations

import os
from typing import Any

from cloud.base import CloudProvider, CloudProviderError


class CloudProviderRegistry:
    """Mutable registry of infrastructure backends available to Alice."""

    def __init__(self) -> None:
        self._providers: dict[str, CloudProvider] = {}

    def register(self, provider: CloudProvider) -> None:
        self._providers[str(provider.name)] = provider

    def get(self, name: str) -> CloudProvider:
        provider = self._providers.get(str(name))
        if provider is None:
            known = ", ".join(sorted(self._providers)) or "<none>"
            raise CloudProviderError(
                f"Unknown cloud provider: {name}. Registered providers: {known}",
                code="unsupported_provider",
            )
        return provider

    def names(self) -> list[str]:
        return sorted(self._providers)


_registry = CloudProviderRegistry()


def get_registry() -> CloudProviderRegistry:
    return _registry


def ensure_default_providers() -> CloudProviderRegistry:
    """Register Alice Cloud without contacting a runtime during import."""

    from cloud.alice.provider import AliceCloudProvider

    registry = get_registry()
    if "alice" not in registry.names():
        registry.register(AliceCloudProvider())
    return registry


def resolve_provider_name(args: dict[str, Any]) -> str:
    """Resolve an explicit provider, environment override, or Alice Cloud."""

    value = args.get("provider")
    if isinstance(value, str) and value.strip():
        return value.strip().lower()
    return os.getenv("ALICE_CLOUD_PROVIDER", "").strip().lower() or "alice"

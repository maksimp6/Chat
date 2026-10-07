"""Runtime registry for optional infrastructure providers."""

from __future__ import annotations

import os
from typing import Any

from cloud.base import CloudProvider, CloudProviderError


class CloudProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, CloudProvider] = {}

    def register(self, provider: CloudProvider) -> None:
        self._providers[str(provider.name)] = provider

    def get(self, name: str) -> CloudProvider:
        provider = self._providers.get(str(name))
        if provider is None:
            known = ", ".join(sorted(self._providers)) or "<none>"
            raise CloudProviderError(f"Unknown cloud provider: {name}. Registered providers: {known}", code="unsupported_provider")
        return provider

    def names(self) -> list[str]:
        return sorted(self._providers)


_registry = CloudProviderRegistry()

def get_registry() -> CloudProviderRegistry:
    return _registry

def ensure_default_providers() -> CloudProviderRegistry:
    return get_registry()

def resolve_provider_name(args: dict[str, Any]) -> str:
    value = args.get("provider")
    if isinstance(value, str) and value.strip():
        return value.strip().lower()
    value = os.getenv("ALICE_CLOUD_PROVIDER", "").strip().lower()
    if not value:
        raise CloudProviderError("No cloud provider is configured", code="provider_not_configured")
    return value

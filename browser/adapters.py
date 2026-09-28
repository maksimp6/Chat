"""Adapter boundary for Alice's cloud and local browser capabilities.

The registry is deliberately explicit: an adapter is unavailable until the
runtime registers it. This prevents a tool contract from implying access to a
real browser, cloud session, credentials or cookies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from .capabilities import (
    BROWSER_CAPABILITY_CONTRACTS,
    BrowserAction,
    sanitize_browser_value,
    validate_browser_action,
)


class BrowserAdapter(Protocol):
    """Minimal adapter interface implemented by a cloud or emulator backend."""

    def execute(self, action: BrowserAction) -> Mapping[str, Any]:
        """Execute one already-authorized browser action."""


@dataclass(frozen=True)
class BrowserAdapterResult:
    """Sanitized, transport-neutral result returned by an adapter."""

    success: bool
    data: Any = None
    error: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_mapping(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "data": sanitize_browser_value(self.data),
            "error": self.error,
            "metadata": sanitize_browser_value(dict(self.metadata)),
        }


class BrowserAdapterRegistry:
    """Explicit capability-to-adapter map used by the unified tool boundary."""

    def __init__(self, adapters: Mapping[str, BrowserAdapter] | None = None):
        self._adapters = dict(adapters or {})

    def register(self, capability: str, adapter: BrowserAdapter) -> None:
        if capability not in BROWSER_CAPABILITY_CONTRACTS:
            raise ValueError(f"unknown browser capability: {capability}")
        self._adapters[capability] = adapter

    def execute(self, action: BrowserAction) -> dict[str, Any]:
        errors = validate_browser_action(action)
        if errors:
            return BrowserAdapterResult(
                False,
                error="; ".join(errors),
                metadata={"phase": "validation", "capability": action.capability},
            ).to_mapping()

        adapter = self._adapters.get(action.capability)
        if adapter is None:
            return BrowserAdapterResult(
                False,
                error=f"Browser adapter unavailable: {action.capability}",
                metadata={"phase": "adapter_unavailable", "capability": action.capability},
            ).to_mapping()

        try:
            raw_result = adapter.execute(action)
        except Exception as exc:
            return BrowserAdapterResult(
                False,
                error=f"{type(exc).__name__}: {exc}",
                metadata={"phase": "execution", "capability": action.capability},
            ).to_mapping()

        if not isinstance(raw_result, Mapping):
            return BrowserAdapterResult(
                False,
                error="Browser adapter returned a non-mapping result",
                metadata={"phase": "result_validation", "capability": action.capability},
            ).to_mapping()

        return BrowserAdapterResult(
            bool(raw_result.get("success", True)),
            data=raw_result.get("data"),
            error=raw_result.get("error"),
            metadata={
                "capability": action.capability,
                **dict(raw_result.get("metadata") or {}),
            },
        ).to_mapping()


def register_browser_tools(tool_registry: Any, adapters: BrowserAdapterRegistry) -> None:
    """Opt in browser contracts to a ToolRegistry through one dispatcher boundary."""

    for name, contract in BROWSER_CAPABILITY_CONTRACTS.items():

        def execute(arguments: Mapping[str, Any], _config: Mapping[str, Any], *, _name=name):
            action = BrowserAction(
                capability=_name,
                action=str(arguments.get("action") or ""),
                target=str(arguments.get("target") or ""),
                value=arguments.get("value"),
            )
            return adapters.execute(action)

        definition = contract.to_definition()
        definition["func"] = execute
        tool_registry.register("browser", name, definition)

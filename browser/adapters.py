"""Adapter boundary for Logos's cloud and local browser capabilities.

The registry is deliberately explicit: an adapter is unavailable until the
runtime registers it. This prevents a tool contract from implying access to a
real browser, cloud session, credentials or cookies.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from browser.capabilities import (
    BROWSER_CAPABILITY_CONTRACTS,
    BrowserAction,
    sanitize_browser_value,
    validate_browser_action,
)

# Worker error text can echo page content or request headers, so credentials are redacted and
# the text is bounded before it reaches a trace, a log or the model.
# The value runs to the end of the line: "Authorization: Bearer <key>" and a cookie list have more
# than one token, and stopping at the first space would leave the credential in the text.
_CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?i)\b(authorization|cookie|token|password|secret|api[_-]?key)\s*[:=]\s*[^\r\n]+"
)
_MAX_ERROR_CHARS = 500


def _sanitize_error(value: str | None) -> str | None:
    if value is None:
        return None
    text = _CREDENTIAL_ASSIGNMENT.sub(lambda match: match.group(1) + "=<redacted>", str(value))
    return text[:_MAX_ERROR_CHARS]


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
            "error": _sanitize_error(self.error),
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

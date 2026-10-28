"""Contracts shared by Logos's cloud and local browsing adapters.

This module deliberately contains no browser driver. Adapters must execute
these contracts through UniversalToolExecutor and provide their own capability
implementation. Keeping the contract separate prevents a local emulator from
silently gaining cloud-session or credential access.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


BROWSER_ACTIONS = (
    "navigate",
    "inspect",
    "screenshot",
    "click",
    "fill",
    "assert_state",
)

_SENSITIVE_KEY_PARTS = (
    "authorization",
    "cookie",
    "password",
    "secret",
    "token",
    "otp",
    "session",
    "credential",
)


@dataclass(frozen=True)
class BrowserCapabilityContract:
    """Public contract for one isolated browsing capability."""

    name: str
    mode: str
    actions: tuple[str, ...] = BROWSER_ACTIONS
    allowed_scopes: tuple[str, ...] = ()
    approval_actions: tuple[str, ...] = ("click", "fill")
    requires_emulator: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.mode not in {"cloud", "local"}:
            raise ValueError("browser capability mode must be cloud or local")
        if not self.name:
            raise ValueError("browser capability name is required")
        unknown = set(self.actions) - set(BROWSER_ACTIONS)
        if unknown:
            raise ValueError(f"unsupported browser actions: {sorted(unknown)}")
        if not self.allowed_scopes:
            raise ValueError("browser capability must declare an allowed scope")

CLOUD_BROWSING = BrowserCapabilityContract(
    name="browser_cloud",
    mode="cloud",
    allowed_scopes=("approved_cloud_session",),
    metadata={
        "credentials_model_visible": False,
        "real_profile_access": False,
        "trace_redact_arguments": ["value"],
    },
)

LOCAL_BROWSING = BrowserCapabilityContract(
    name="browser_local",
    mode="local",
    allowed_scopes=("project_runtime",),
    requires_emulator=True,
    metadata={
        "credentials_model_visible": False,
        "real_profile_access": False,
        "trace_redact_arguments": ["value"],
    },
)

BROWSER_CAPABILITY_CONTRACTS = {
    CLOUD_BROWSING.name: CLOUD_BROWSING,
    LOCAL_BROWSING.name: LOCAL_BROWSING,
}


@dataclass(frozen=True)
class BrowserAction:
    """Validated action envelope passed from an adapter to the executor."""

    capability: str
    action: str
    target: str
    value: str | None = None

def validate_browser_action(action: BrowserAction) -> list[str]:
    contract = BROWSER_CAPABILITY_CONTRACTS.get(action.capability)
    if contract is None:
        return [f"unknown browser capability: {action.capability}"]
    errors: list[str] = []
    if action.action not in contract.actions:
        errors.append(f"action is not supported by {action.capability}: {action.action}")
    if not action.target.strip():
        errors.append("browser action target is required")
    return errors


# Usage counters contain "token" but are not credentials, and the budget reads them after redaction.
_TOKEN_COUNTER_KEYS = frozenset(
    {
        "consumed_tokens",
        "total_tokens",
        "token_usage",
        "input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "cached_tokens",
        "tool_tokens",
    }
)


def _sensitive_key(key: str) -> bool:
    lowered = key.lower().replace("-", "_")
    if lowered in _TOKEN_COUNTER_KEYS:
        return False
    return any(part in lowered for part in _SENSITIVE_KEY_PARTS)


def sanitize_browser_value(value: Any) -> Any:
    """Redact credentials and cookies before traces, logs or issue comments."""
    if isinstance(value, Mapping):
        return {
            str(key): "<redacted>" if _sensitive_key(str(key)) else sanitize_browser_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize_browser_value(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_browser_value(item) for item in value]
    return value


__all__ = [
    "BROWSER_ACTIONS",
    "BROWSER_CAPABILITY_CONTRACTS",
    "BrowserAction",
    "BrowserCapabilityContract",
    "CLOUD_BROWSING",
    "LOCAL_BROWSING",
    "sanitize_browser_value",
    "validate_browser_action",
]

# Vendored from tool_providers/read_only_policy.py. Do not edit this copy: change the canonical file
# and run `python3 scripts/check_independence.py --sync-vendored`.
"""Read-only tool allowlist policy.

Pure and dependency-free, so units that refine it (agent_runtime's review policy)
use a vendored copy instead of importing the tool platform.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ReadOnlyToolPolicy:
    """Trusted per-invocation allowlist, supplied outside model-controlled data."""

    allowed_tools: frozenset[str]
    name: str = "read-only"

    def __post_init__(self) -> None:
        object.__setattr__(self, "allowed_tools", frozenset(self.allowed_tools))

    def allows_tool(self, definition: Any) -> bool:
        return (
            definition.name in self.allowed_tools
            and definition.read_only
            and str(definition.executor.get("type") or "local") == "local"
        )

    def __call__(self, definition: Any, call: Any) -> bool:
        return self.allows_tool(definition)

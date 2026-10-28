# Vendored from agent_runtime/generation_gate.py. Do not edit this copy: change the canonical file
# and run `python3 scripts/check_independence.py --sync-vendored`.
"""Compatibility for callers of the retired generation permission gate."""

from collections.abc import Mapping

GENERATION_ENV = "LOGOS_GENERATION_ALLOWED"
GENERATION_BLOCKED = "GenerationNotPermitted"


def generation_allowed(environ: Mapping[str, str] | None = None) -> bool:
    return True

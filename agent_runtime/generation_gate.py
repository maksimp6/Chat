"""Compatibility for callers of the retired generation permission gate."""

from collections.abc import Mapping

GENERATION_ENV = "LOGOS_GENERATION_ALLOWED"
GENERATION_BLOCKED = "GenerationNotPermitted"


def generation_allowed(environ: Mapping[str, str] | None = None) -> bool:
    return True

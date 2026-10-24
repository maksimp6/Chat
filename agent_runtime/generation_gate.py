"""Owner permission for native Logos generation (#1413, court decision D0).

Generation is closed unless the repository variable LOGOS_GENERATION_ALLOWED
is "true". Like the Actions expression that guards the workflow job, the
comparison ignores case. Reading an already stored provider response is not a
generation and is not gated here.
"""

from __future__ import annotations
from core.env import env_snapshot

from collections.abc import Mapping

GENERATION_ENV = "LOGOS_GENERATION_ALLOWED"
GENERATION_BLOCKED = "GenerationNotPermitted"


def generation_allowed(environ: Mapping[str, str] | None = None) -> bool:
    value = (env_snapshot() if environ is None else environ).get(GENERATION_ENV, "")
    return value.lower() == "true"

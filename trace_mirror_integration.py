"""Best-effort integration of the Supabase trace mirror."""
from __future__ import annotations

import logging
from typing import Any, Mapping

from supabase_trace_mirror import mirror_trace

logger = logging.getLogger(__name__)


def mirror_finalized_trace(trace: Mapping[str, Any]) -> bool:
    """Mirror a finalized trace without affecting the request path."""
    try:
        return mirror_trace(trace)
    except Exception:  # pragma: no cover
        logger.warning("Unexpected execution trace mirror failure", exc_info=True)
        return False


def install_execution_trace_hook() -> None:
    """Install the mirror hook once on ExecutionTrace.finalize()."""
    from trace_manager import ExecutionTrace

    if getattr(ExecutionTrace, "_supabase_mirror_hook_installed", False):
        logger.debug("Supabase execution trace mirror hook is already installed")
        return

    original_finalize = ExecutionTrace.finalize

    def finalize_with_mirror(self: ExecutionTrace) -> dict[str, Any]:
        result = original_finalize(self)
        if not getattr(self, "_supabase_trace_mirrored", False):
            self._supabase_trace_mirrored = True
            mirror_finalized_trace(result)
        return result

    ExecutionTrace.finalize = finalize_with_mirror
    ExecutionTrace._supabase_mirror_hook_installed = True
    logger.info("Installed Supabase execution trace mirror hook")


install_execution_trace_hook()

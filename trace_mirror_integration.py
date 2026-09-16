"""Helpers for mirroring finalized execution traces without breaking requests."""
from __future__ import annotations

import logging
from typing import Any, Mapping

from supabase_trace_mirror import mirror_trace

logger = logging.getLogger(__name__)


def mirror_finalized_trace(trace: Mapping[str, Any]) -> bool:
    """Best-effort mirror of an already finalized trace.

    The helper deliberately swallows mirror failures because persistence in an
    optional observability sink must never turn a successful chat request into
    an HTTP 500 response.
    """
    try:
        return mirror_trace(trace)
    except Exception:  # pragma: no cover - defensive boundary
        logger.warning("Unexpected execution trace mirror failure", exc_info=True)
        return False

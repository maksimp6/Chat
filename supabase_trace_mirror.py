"""Optional, best-effort Supabase mirror for execution traces.

The mirror is deliberately independent from the main request path: callers should
invoke ``mirror_trace`` without allowing mirror failures to escape.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Mapping, Optional
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)


def mirror_trace(trace: Mapping[str, Any], *, timeout: float = 2.0) -> bool:
    """Insert a sanitized trace into Supabase when configured.

    Returns False when disabled or when the mirror fails. No exception is raised.
    This uses the REST endpoint and a publishable/anon key only; service-role keys
    must never be placed in client or repository configuration.
    """
    url = os.getenv("SUPABASE_URL", "").rstrip("/")
    key = os.getenv("SUPABASE_ANON_KEY", "")
    if not url or not key:
        return False

    trace_id = str(trace.get("trace_id", ""))
    if not trace_id:
        return False

    row = {
        "trace_id": trace_id,
        "created_at": trace.get("created_at"),
        "payload": trace,
    }
    request = Request(
        f"{url}/rest/v1/execution_traces",
        data=json.dumps(row, ensure_ascii=False).encode("utf-8"),
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return 200 <= response.status < 300
    except Exception:  # pragma: no cover - logging path
        logger.warning("Supabase trace mirror failed", exc_info=True)
        return False

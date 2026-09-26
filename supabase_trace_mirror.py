"""Optional, best-effort Supabase mirror for execution traces.

The mirror is intentionally independent from the main request path. Callers may
invoke :func:`mirror_trace` without allowing mirror failures to escape.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Mapping
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)


def _json_safe(value: Any, *, _seen: set[int] | None = None) -> Any:
    """Convert a trace payload into JSON-safe data without following cycles."""
    if _seen is None:
        _seen = set()

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "<binary data omitted>"

    object_id = id(value)
    if object_id in _seen:
        return "<cycle omitted>"

    if isinstance(value, Mapping):
        _seen.add(object_id)
        try:
            return {str(key): _json_safe(item, _seen=_seen) for key, item in value.items()}
        finally:
            _seen.remove(object_id)

    if isinstance(value, (list, tuple, set, frozenset)):
        _seen.add(object_id)
        try:
            return [_json_safe(item, _seen=_seen) for item in value]
        finally:
            _seen.remove(object_id)

    return str(value)


def mirror_trace(trace: Mapping[str, Any], *, timeout: float = 2.0) -> bool:
    """Insert a sanitized trace into Supabase when server credentials are configured.

    The operation is disabled unless ``SUPABASE_URL`` and the backend-only
    ``SUPABASE_SECRET_KEY`` are present. The secret key is supplied by CI from
    the Supabase Management API and is never committed or exposed to the client.
    Returns ``False`` on configuration or network errors and never raises.
    """
    url = os.getenv("SUPABASE_URL", "").rstrip("/")
    key = os.getenv("SUPABASE_SECRET_KEY", "")
    if not url or not key:
        return False

    safe_trace = _json_safe(trace)
    if not isinstance(safe_trace, dict):
        return False

    trace_id = str(safe_trace.get("trace_id", ""))
    if not trace_id:
        return False

    row = {
        "trace_id": trace_id,
        "created_at": safe_trace.get("created_at"),
        "payload": safe_trace,
    }

    request = Request(
        f"{url}/rest/v1/execution_traces",
        data=json.dumps(row, ensure_ascii=False, allow_nan=False).encode("utf-8"),
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates,return=minimal",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=timeout) as response:
            return 200 <= response.status < 300
    except Exception:  # pragma: no cover - defensive logging path
        logger.warning("Supabase trace mirror failed", exc_info=True)
        return False

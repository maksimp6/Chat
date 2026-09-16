"""Startup diagnostics for the optional Supabase trace mirror."""
from __future__ import annotations

import logging
import os
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

logger = logging.getLogger("alice_app.supabase")


def _safe_detail(detail: str, secret: str) -> str:
    """Remove credentials from diagnostic text before it reaches the logs."""
    detail = detail.replace(secret, "[REDACTED]") if secret else detail
    return " ".join(detail.split())[:1000]


def _http_error_detail(exc: HTTPError, secret: str) -> str:
    """Return a bounded, secret-free HTTP error description."""
    try:
        body = exc.read().decode("utf-8", errors="replace")
    except Exception:
        body = ""
    body = _safe_detail(body, secret)
    return f"HTTP {exc.code} {exc.reason}" + (f": {body}" if body else "")


def check_supabase_trace_mirror(*, timeout: float = 3.0) -> str:
    """Check Supabase mirror configuration and table reachability.

    Returns one of ``disabled``, ``configured``, ``ready`` or ``error``.
    Secrets are never included in log messages. A failed check is non-fatal.
    """
    base_url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()

    if not base_url or not service_key:
        logger.info("Supabase trace mirror: disabled (credentials are not configured)")
        return "disabled"

    parsed = urlparse(base_url)
    if parsed.scheme != "https" or not parsed.netloc:
        logger.warning("Supabase trace mirror: error (SUPABASE_URL must be an HTTPS URL)")
        return "error"

    logger.info("Supabase trace mirror: configured")
    endpoint = f"{base_url}/rest/v1/execution_traces?select=trace_id&limit=1"
    request = Request(
        endpoint,
        headers={
            "apikey": service_key,
            "Authorization": f"Bearer {service_key}",
            "Accept": "application/json",
        },
        method="GET",
    )

    try:
        with urlopen(request, timeout=timeout) as response:
            if 200 <= response.status < 300:
                logger.info("Supabase trace mirror: ready")
                return "ready"
            logger.warning(
                "Supabase trace mirror: error (HTTP %s)", response.status
            )
    except HTTPError as exc:
        detail = _http_error_detail(exc, service_key)
        logger.warning(
            "Supabase trace mirror: error (HTTP failure; %s; exception=%s)",
            detail,
            type(exc).__name__,
        )
    except Exception as exc:
        detail = _safe_detail(str(exc) or "no additional details", service_key)
        logger.warning(
            "Supabase trace mirror: error (exception=%s; detail=%s)",
            type(exc).__name__,
            detail,
        )
    return "error"

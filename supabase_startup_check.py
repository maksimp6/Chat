"""Startup diagnostics for the optional Supabase trace mirror."""
from __future__ import annotations

import logging
import os
from urllib.parse import urlparse
from urllib.request import Request, urlopen

logger = logging.getLogger("alice_app.supabase")


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
            logger.warning("Supabase trace mirror: error (HTTP %s)", response.status)
    except Exception as exc:
        status = getattr(exc, "code", None)
        if status in (401, 403):
            reason = "credentials rejected or table access denied"
        elif status == 404:
            reason = "execution_traces table or endpoint not found"
        else:
            reason = "endpoint unavailable"
        logger.warning("Supabase trace mirror: error (%s)", reason)
    return "error"

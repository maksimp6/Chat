"""Redirect for the verification URL of an existing RDC device flow.

The device owns PKCE and polling. This module never receives provider tokens.
"""

import json
import os
import time
from pathlib import Path
from urllib.parse import urlsplit

from flask import jsonify, redirect


def verification_url(path, now=None):
    """Read a bounded, expiring handoff written by the trusted deployment job."""
    now = time.time() if now is None else now
    with Path(path).open("rb") as handle:
        raw = handle.read(4097)
    if len(raw) > 4096:
        raise ValueError("oversized handoff")
    value = json.loads(raw)
    expires = value["expires_at"]
    if isinstance(expires, bool) or not isinstance(expires, (int, float)):
        raise TypeError("invalid expiry")
    if not now < expires <= now + 600:
        raise ValueError("expired handoff")
    url = value["verification_uri_complete"]
    if not isinstance(url, str) or any(
        ord(char) <= 32 or ord(char) == 127 for char in url
    ):
        raise ValueError("invalid URL")
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname
        not in {"mcp.desktopcommander.app", "auth.desktopcommander.app"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in {None, 443}
        or parsed.fragment
    ):
        raise ValueError("untrusted verification URL")
    return url


def install_rdc_pairing(app):
    @app.get("/rdc")
    def rdc_pairing_redirect():
        path = os.environ.get("LOGOS_RDC_PAIRING_FILE", "")
        try:
            if not path:
                raise ValueError("handoff not configured")
            response = redirect(verification_url(path), code=303)
        except (OSError, ValueError, KeyError, TypeError):
            response = jsonify(
                {"error": "RDC verification link unavailable or expired"}
            )
            response.status_code = 503
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

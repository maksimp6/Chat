"""Resolve the server-side identity used by Treasury operations.

Client-supplied owner_id values are never trusted. In authenticated requests,
ownership comes from a server-side Flask auth context or from the bootstrap
bearer token stored in the HttpOnly identity cookie/header. The fixed
ALICE_OWNER_ID fallback remains available for explicitly configured
single-user deployments.
"""

import os
from typing import Optional

from flask import g, has_request_context, request

from user_identity import authenticate_user_token


class TreasuryIdentityError(ValueError):
    """Raised when a Treasury operation has no trusted owner identity."""


def get_current_owner_id(*, required: bool = True) -> Optional[str]:
    """Return a trusted owner id without accepting client-controlled values."""
    owner_id = None

    if has_request_context():
        owner_id = getattr(g, "authenticated_user_id", None) or getattr(g, "user_id", None)

        token = request.headers.get("X-Alice-User-Token") or request.cookies.get("alice_user_token")
        if token:
            token_owner_id = authenticate_user_token(token)
            if token_owner_id is None:
                raise TreasuryIdentityError("invalid authenticated owner token")
            owner_id = token_owner_id

    if owner_id is None:
        owner_id = os.getenv("ALICE_OWNER_ID")

    if owner_id is None or not str(owner_id).strip():
        if required:
            raise TreasuryIdentityError("authenticated owner identity is required")
        return None

    return str(owner_id).strip()

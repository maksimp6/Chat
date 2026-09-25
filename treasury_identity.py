"""Resolve the server-side identity used by Treasury operations.

Client-supplied owner_id values are never trusted. Treasury ownership comes
only from a trusted server-side Flask auth context or the explicit
ALICE_OWNER_ID deployment identity.
"""

import os
from typing import Optional

from flask import g, has_request_context


class TreasuryIdentityError(ValueError):
    """Raised when a Treasury operation has no trusted owner identity."""


def get_current_owner_id(*, required: bool = True) -> Optional[str]:
    """Return a trusted owner id without accepting client-controlled values."""
    owner_id = None

    if has_request_context():
        owner_id = (
            getattr(g, "authenticated_user_id", None)
            or getattr(g, "user_id", None)
        )

    if owner_id is None:
        owner_id = os.getenv("ALICE_OWNER_ID")

    if owner_id is None or not str(owner_id).strip():
        if required:
            raise TreasuryIdentityError("authenticated owner identity is required")
        return None

    return str(owner_id).strip()

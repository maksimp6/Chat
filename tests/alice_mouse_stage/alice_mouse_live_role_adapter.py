"""Staging adapter: derive roles from server-owned credentials, never request JSON.

The existing Live Server has a single legacy Bearer token; it grants NO
mouse-control role. Controller credentials must be separately provisioned
by the server operator before any signed mouse grant can be issued.
"""

from __future__ import annotations
import hmac
import os
import stat
from pathlib import Path


class LiveRoleAuthority:
    def __init__(self, legacy_token: str, controller_token_path: Path):
        self._legacy = legacy_token
        self._controller_path = controller_token_path

    def authenticate(self, authorization: str) -> str | None:
        if not isinstance(authorization, str) or not authorization.startswith("Bearer "):
            return None
        supplied = authorization[7:]
        if not supplied:
            return None
        if hmac.compare_digest(supplied, self._legacy):
            return "viewer"
        try:
            fd = os.open(self._controller_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
            try:
                st = os.fstat(fd)
                if (
                    not stat.S_ISREG(st.st_mode)
                    or st.st_nlink != 1
                    or st.st_uid != os.getuid()
                    or stat.S_IMODE(st.st_mode) != 0o600
                    or not 32 <= st.st_size <= 256
                ):
                    return None
                raw = os.read(fd, 257)
                controller = raw.decode("utf-8").strip()
                if len(controller) < 32:
                    return None
            finally:
                os.close(fd)
        except (OSError, UnicodeError):
            return None
        return "controller" if hmac.compare_digest(supplied, controller) else None

    def authorize_command(self, authorization: str, data: dict) -> bool:
        # Never use client-asserted role, principal, or administrative flags.
        if not isinstance(data, dict):
            return False
        if any(k in data for k in ("role", "principal", "admin", "is_controller")):
            return False
        return self.authenticate(authorization) == "controller"

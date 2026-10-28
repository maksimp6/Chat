#!/usr/bin/env python3
"""Rotate the deployment-wide Cloud.ru Foundation Models API key.

Cloud.ru reissues the existing API-key resource, changing its secret and
expiration while retaining the provider key ID. The replacement is validated
against Foundation Models before the local credential is promoted.
The logic is provider_accounts.key_rotation_cli.rotate_cloudru_key; this file is
its composition root.
"""

from __future__ import annotations

import logging

from composition import wiring
from provider_accounts.key_rotation_cli import cloudru_key_ttl as _ttl
from provider_accounts.key_rotation_cli import rotate_cloudru_key

logging.basicConfig(level=logging.INFO)

__all__ = ["_ttl", "main"]


def main() -> int:
    return rotate_cloudru_key()


if __name__ == "__main__":
    wiring.register_all()
    raise SystemExit(main())

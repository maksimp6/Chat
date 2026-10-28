#!/usr/bin/env python3
"""Rotate the single deployment-wide Yandex API key.

Run this worker at least hourly. It rotates when the active key has one hour or
less remaining. The generated replacement is valid for exactly 12 hours.
The logic is provider_accounts.key_rotation_cli.rotate_yandex_key; this file is
its composition root.
"""

from __future__ import annotations

import logging

from composition import wiring
from provider_accounts.key_rotation_cli import rotate_yandex_key

logging.basicConfig(level=logging.INFO)


def main() -> int:
    return rotate_yandex_key()


if __name__ == "__main__":
    wiring.register_all()
    raise SystemExit(main())

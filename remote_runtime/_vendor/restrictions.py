# Vendored from core/restrictions.py. Do not edit this copy: change the canonical file
# and run `python3 scripts/check_independence.py --sync-vendored`.
"""Compatibility for callers of the retired restriction switch."""


def restrictions_lifted() -> bool:
    """Built-in approval, quota, budget and tool access gates have been removed."""
    return True

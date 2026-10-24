"""Compatibility for callers of the retired restriction switch."""


def restrictions_lifted() -> bool:
    """Built-in approval, quota, budget and tool access gates have been removed."""
    return True

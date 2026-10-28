# Vendored from core/output.py. Do not edit this copy: change the canonical file
# and run `python3 scripts/check_independence.py --sync-vendored`.
"""Byte-bounded text for command output, with a visible truncation marker."""

from __future__ import annotations


def limit_output(value: str | bytes | None, max_bytes: int = 1048576) -> str:
    if value is None:
        return ""
    text = (
        value.decode("utf-8", errors="replace")
        if isinstance(value, bytes)
        else str(value)
    )
    raw = text.encode("utf-8", errors="replace")
    if len(raw) <= max_bytes:
        return text
    suffix = "\n...[output truncated]"
    suffix_bytes = suffix.encode("utf-8")
    budget = max(0, max_bytes - len(suffix_bytes))
    return raw[:budget].decode("utf-8", errors="ignore") + suffix

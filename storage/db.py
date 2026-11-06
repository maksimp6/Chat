"""Former name of :mod:`storage.records`; a check scenario still imports these two names here."""

from __future__ import annotations

from storage.records import get_messages, get_store

__all__ = ["get_messages", "get_store"]

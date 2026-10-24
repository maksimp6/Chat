"""Binds the 3D-printing domain to Logos storage and identity."""

from printing3d.store import PrintStore
from storage.db import get_store


def printing_store() -> PrintStore:
    """Return the shared native store; Logos owns its single writer."""
    return get_store()


def init_printing_tables() -> None:
    """Open the native store once at startup so a missing journal fails fast."""
    printing_store()

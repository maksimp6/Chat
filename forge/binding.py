"""Binds the 3D-printing domain to Logos storage and identity."""

from printing3d.store import PrintStore
from forge.host import get_store


def printing_store() -> PrintStore:
    """Return the shared native store; Logos owns its single writer."""
    return get_store()


def prepare_printing_records() -> None:
    """Open the native store once at startup so a missing journal fails fast."""
    printing_store()


# Former name, kept for existing callers.
init_printing_tables = prepare_printing_records

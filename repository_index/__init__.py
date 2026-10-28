"""Shared contracts for immutable repository indexes."""

from repository_index.provenance import (
    SCHEMA_VERSION,
    SNAPSHOT_SCHEMA_VERSION,
    snapshot_revision,
)

__all__ = ["SCHEMA_VERSION", "SNAPSHOT_SCHEMA_VERSION", "snapshot_revision"]

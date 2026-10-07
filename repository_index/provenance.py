"""Validation of immutable repository-index snapshot identity."""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any

SCHEMA_VERSION = 1
SNAPSHOT_SCHEMA_VERSION = 1


def _has_snapshot_provenance(index: Mapping[str, Any]) -> bool:
    markers: set[str] = {
        "snapshot_schema_version",
        "parser_version",
        "repository",
        "source_version",
        "source_kind",
        "skipped_paths",
    }
    if markers.intersection(index):
        return True
    for item in index.get("files") or []:
        if not isinstance(item, Mapping):
            raise ValueError("invalid repository index entry")
        if {"git_blob_oid", "source_id", "source_ref"}.intersection(item):
            return True
        for symbol in item.get("symbols") or []:
            if not isinstance(symbol, Mapping):
                raise ValueError("invalid repository index symbol")
            if "source_ref" in symbol:
                return True
    return False


def snapshot_revision(index: Mapping[str, Any]) -> str | None:
    """Validate snapshot identity, returning None only for genuine legacy input."""
    if not _has_snapshot_provenance(index):
        return None
    versions: dict[str, int] = {
        "schema_version": SCHEMA_VERSION,
        "snapshot_schema_version": SNAPSHOT_SCHEMA_VERSION,
    }
    for key, expected in versions.items():
        if type(index.get(key)) is not int or index[key] != expected:
            raise ValueError("unsupported or incomplete snapshot schema")
    if index.get("source_kind") != "git_commit":
        raise ValueError("unsupported snapshot source kind")
    patterns: dict[str, str] = {
        "source_version": r"[0-9a-f]{40}|[0-9a-f]{64}",
        "repository": r"[a-z0-9_-][a-z0-9_.-]*/[a-z0-9_-][a-z0-9_.-]*",
    }
    for key, pattern in patterns.items():
        value = index.get(key)
        if not isinstance(value, str) or not re.fullmatch(pattern, value):
            raise ValueError("invalid snapshot identity")
    parser_version = index.get("parser_version")
    if not isinstance(parser_version, str) or not parser_version.strip():
        raise ValueError("missing snapshot parser version")
    return str(index["source_version"])

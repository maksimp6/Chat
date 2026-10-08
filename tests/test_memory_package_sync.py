"""Prevent the standalone distribution copy from diverging from tested engine."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_packaged_engine_matches_tested_source():
    for filename in ("__init__.py", "store.py"):
        source = (ROOT / "memory_engine" / filename).read_bytes()
        packaged = (ROOT / "memory-db-package" / "memory_engine" / filename).read_bytes()
        assert packaged == source, f"Memory DB distribution drift: {filename}"

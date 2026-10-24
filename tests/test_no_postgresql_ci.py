"""Ensure CI cannot silently reintroduce PostgreSQL test infrastructure."""

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]




def test_no_destructive_postgres_test_fixture():
    assert not (ROOT / "tests/postgres_test_guard.py").exists()
    assert not (ROOT / "tests/test_postgres_integration.py").exists()



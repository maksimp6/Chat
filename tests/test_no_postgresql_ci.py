"""Ensure CI cannot silently reintroduce PostgreSQL test infrastructure."""

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_ci_has_no_postgresql_service_or_job():
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    jobs = workflow["jobs"]
    assert "postgres" not in jobs
    assert "postgres" not in jobs["required"]["needs"]
    assert "database" not in jobs["changes"]["outputs"]
    assert all("postgres" not in job.get("services", {}) for job in jobs.values())
    assert "postgresql://" not in (ROOT / ".github/workflows/ci.yml").read_text()


def test_no_destructive_postgres_test_fixture():
    assert not (ROOT / "tests/postgres_test_guard.py").exists()
    assert not (ROOT / "tests/test_postgres_integration.py").exists()


def test_merge_readiness_does_not_require_deleted_postgres_job():
    workflow = (ROOT / ".github/workflows/merge-readiness.yml").read_text()
    assert "PostgreSQL integration" not in workflow
    assert "CI required" in workflow

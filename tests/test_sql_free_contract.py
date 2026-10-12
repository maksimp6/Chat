"""Fail-closed red contract for the SQL-free Alice Pro runtime.

This intentionally fails until runtime and CI have been migrated to MemoryStore.
Keep the accepted memory_engine journal/snapshot implementation in scope.
"""

import ast
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = (
    "db.py",
    "db_backend.py",
    "memory_db.py",
    "mcp_storage.py",
    "storage.py",
    "runtime/storage.py",
    "agent_memory/runtime_store.py",
    "agent_memory/repository.py",
    "agent_shell/store.py",
)
FORBIDDEN_IMPORTS = {"sqlite3", "psycopg", "psycopg2", "sqlalchemy"}
LEGACY_FILES = (
    "agent_memory/file_memory_db.py",
    "agent_memory/conversation_ownership_migration.py",
    "agent_memory/user_identity_migration.py",
    "mcp_migration.sql",
    "observability_migrations.py",
    "runtime_migrations.py",
    "requirements-postgres.txt",
    "docker-compose.postgres.yml",
)


def test_no_sql_driver_imports_in_runtime():
    offenders = []
    for relative in RUNTIME:
        path = ROOT / relative
        if not path.exists():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            else:
                continue
            if any(name.split(".")[0] in FORBIDDEN_IMPORTS for name in names):
                offenders.append(relative)
                break
    assert not offenders, f"SQL driver imports remain: {offenders}"


def test_legacy_sql_and_filememorydb_are_absent():
    offenders = [name for name in LEGACY_FILES if (ROOT / name).exists()]
    offenders.extend(str(path.relative_to(ROOT)) for path in (ROOT / "migrations").glob("*.sql"))
    assert not offenders, f"Legacy files remain: {offenders}"


def test_ci_has_no_postgresql_job_or_required_dependency():
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    jobs = workflow["jobs"]
    assert "postgres" not in jobs
    assert "postgres" not in jobs["required"]["needs"]
    assert "postgres" not in (ROOT / "scripts/ci_platform_changes.py").read_text(encoding="utf-8")


def test_new_memory_store_is_the_only_accepted_engine():
    source = (ROOT / "memory_engine/store.py").read_text(encoding="utf-8")
    assert "class MemoryStore:" in source
    assert "class _JournalStore:" in source
    assert "import sqlite3" not in source
    assert "import psycopg" not in source

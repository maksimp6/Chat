"""RED desired-state guard: supported Alice runtime must be SQL-free.

Scope deliberately excludes historical docs, archived migration notes, and
third-party vendored code. Legacy SQL tests remain until the replacement is
proven; this guard tests production ownership, not the test fixture language.
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEGACY_SQL_MODULES = {"sqlite3", "psycopg", "psycopg2", "sqlalchemy", "asyncpg"}
LEGACY_SQL_ENTRYPOINTS = ("db_backend.py",)
LEGACY_ENV = ("ALICE_DB_BACKEND", "ALICE_DATABASE_URL", "DATABASE_URL")


def _runtime_python_files():
    """Scan tracked first-party production Python, not tests or tooling."""
    import subprocess

    paths = subprocess.check_output(["git", "ls-files", "*.py"], cwd=ROOT, text=True).splitlines()
    for name in paths:
        path = Path(name)
        if path.parts[0] in {"tests", "scripts", "docs", "tools", "examples"}:
            continue
        if path.name.startswith("test_"):
            continue
        yield ROOT / path


def test_no_sql_runtime_imports():
    """No supported production module may import a SQL engine."""
    offenders = []
    for path in _runtime_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            modules = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            for module in modules:
                if module.split(".")[0] in LEGACY_SQL_MODULES:
                    offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}:{module}")
    assert not offenders, "SQL imports remain in production: " + ", ".join(offenders)


def test_no_legacy_sql_backend_entrypoint():
    """Retire the SQL connection backend instead of retaining a compatibility shim."""
    remaining = [name for name in LEGACY_SQL_ENTRYPOINTS if (ROOT / name).exists()]
    assert not remaining, f"SQL backend files remain: {remaining}"


def test_no_sql_backend_selection_in_runtime():
    """Runtime configuration must not select a SQL database."""
    offenders = []
    for path in _runtime_python_files():
        source = path.read_text(encoding="utf-8")
        for name in LEGACY_ENV:
            if name in source:
                offenders.append(f"{path.relative_to(ROOT)}:{name}")
    assert not offenders, "SQL environment selection remains: " + ", ".join(offenders)


def test_no_direct_sql_connection_calls():
    """Supported runtime must use typed repositories, not get_conn()."""
    offenders = []
    for path in _runtime_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get_conn"
            ):
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert not offenders, "Direct SQL connection calls remain: " + ", ".join(offenders)

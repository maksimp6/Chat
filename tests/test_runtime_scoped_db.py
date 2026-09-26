from pathlib import Path
import sqlite3

import pytest

import db
from runtime import (
    bind_runtime_request,
    current_runtime_data_root,
    current_runtime_id,
)


def _write_probe(value):
    conn = db.get_conn()
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS runtime_probe (value TEXT NOT NULL)")
        conn.execute("DELETE FROM runtime_probe")
        conn.execute("INSERT INTO runtime_probe (value) VALUES (?)", (value,))
        conn.commit()
    finally:
        conn.close()


def _read_probe(path):
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT value FROM runtime_probe").fetchone()[0]
    finally:
        conn.close()


def test_runtime_database_scope_is_private_and_context_is_restored(tmp_path, monkeypatch):
    first_root = tmp_path / "runtime-a"
    second_root = tmp_path / "runtime-b"

    def forbid_postgres_lookup():
        raise AssertionError("runtime database must not consult production PostgreSQL")

    monkeypatch.setattr(db, "postgres_url_from_env", forbid_postgres_lookup)

    with bind_runtime_request("runtime-a", "/preview/a", data_root=str(first_root)):
        assert current_runtime_id() == "runtime-a"
        assert current_runtime_data_root() == str(first_root)
        _write_probe("alpha")

        with bind_runtime_request("runtime-b", "/preview/b", data_root=str(second_root)):
            assert current_runtime_id() == "runtime-b"
            assert current_runtime_data_root() == str(second_root)
            _write_probe("beta")

        assert current_runtime_id() == "runtime-a"
        assert current_runtime_data_root() == str(first_root)

    assert current_runtime_id() is None
    assert current_runtime_data_root() is None
    assert _read_probe(first_root / "alice_pro.db") == "alpha"
    assert _read_probe(second_root / "alice_pro.db") == "beta"


def test_runtime_database_ignores_process_memory_backend(tmp_path, monkeypatch):
    runtime_root = tmp_path / "runtime-memory-override"
    monkeypatch.setenv("ALICE_DB_BACKEND", "memory")
    monkeypatch.setattr(
        db,
        "postgres_url_from_env",
        lambda: (_ for _ in ()).throw(
            AssertionError("runtime database must not consult production PostgreSQL")
        ),
    )

    with bind_runtime_request(
        "runtime-a",
        "/preview/a",
        data_root=str(runtime_root),
    ):
        assert db.is_memory_configured() is False
        _write_probe("sqlite")

    assert Path(runtime_root / "alice_pro.db").is_file()
    assert _read_probe(runtime_root / "alice_pro.db") == "sqlite"


@pytest.mark.parametrize("fail_runtime", [False, True])
def test_host_postgres_routing_is_restored_after_runtime(tmp_path, monkeypatch, fail_runtime):
    database_url = "postgresql://example.invalid/alice_host"
    host_connection = object()
    connections = []
    runtime_root = tmp_path / "runtime-postgres-override"
    monkeypatch.delenv("ALICE_DB_BACKEND", raising=False)
    monkeypatch.setenv("ALICE_DATABASE_URL", database_url)

    def connect_host(url):
        assert current_runtime_id() is None
        assert current_runtime_data_root() is None
        connections.append(url)
        return host_connection

    monkeypatch.setattr(db, "connect_postgres", connect_host)
    assert db.get_conn() is host_connection

    def run_scoped_operation():
        with bind_runtime_request("runtime-a", "/preview/a", data_root=str(runtime_root)):
            _write_probe("private")
            assert connections == [database_url]
            if fail_runtime:
                raise RuntimeError("runtime operation failed")

    if fail_runtime:
        with pytest.raises(RuntimeError, match="runtime operation failed"):
            run_scoped_operation()
    else:
        run_scoped_operation()

    assert current_runtime_id() is None
    assert current_runtime_data_root() is None
    assert db.get_conn() is host_connection
    assert connections == [database_url, database_url]
    assert _read_probe(runtime_root / "alice_pro.db") == "private"

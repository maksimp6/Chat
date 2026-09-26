from pathlib import Path
import sqlite3

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

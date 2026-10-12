"""Real SQLite WAL snapshot consistency plus PostgreSQL transaction contract mocks."""

from __future__ import annotations

import sqlite3

import pytest

from agent_memory.wave1_shadow import ShadowMismatch
from agent_memory.wave1_sql_reader import SnapshotReadError, SqlSnapshotReader


def _source(tmp_path):
    path = tmp_path / "source.sqlite3"
    db = sqlite3.connect(path)
    db.execute("PRAGMA journal_mode = WAL")
    db.executescript(
        """
        CREATE TABLE sessions (id TEXT PRIMARY KEY, status TEXT,
            metadata_json TEXT, created_at INTEGER, updated_at INTEGER, completed_at INTEGER);
        CREATE TABLE global_memory (id INTEGER PRIMARY KEY, category TEXT,
            fact TEXT NOT NULL UNIQUE, updated_at INTEGER);
        CREATE TABLE configs (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO sessions VALUES ('s1','active','{"profile_id":"dev"}',1,1,NULL);
        INSERT INTO sessions VALUES ('s2','completed','{}',2,3,3);
        INSERT INTO global_memory VALUES (1,'architecture','Use file memory',4);
        INSERT INTO configs VALUES ('memory_config','{"enabled":true,"max_context_facts":15}');
        """
    )
    db.commit()
    db.close()
    return path


def test_sqlite_read_only_transaction_revision_is_stable(tmp_path):
    path = _source(tmp_path)
    before = sqlite3.connect(path)
    before_state = tuple(before.iterdump())
    before.close()
    reader = SqlSnapshotReader.sqlite(path)
    first = reader.read()
    second = reader.read()
    assert first.snapshot == second.snapshot
    assert first.owner_audit.status == "unverified_missing"
    assert first.snapshot.source_revision.startswith("sqlite:content-sha256:")
    assert len(first.snapshot.sessions) == 2
    assert len(first.snapshot.facts) == 1
    assert first.snapshot.config == {"enabled": True, "max_context_facts": 15}
    after = sqlite3.connect(path)
    assert tuple(after.iterdump()) == before_state
    after.close()


def test_sqlite_reader_connection_rejects_writes(tmp_path):
    path = _source(tmp_path)
    reader = SqlSnapshotReader.sqlite(path)
    conn = reader._factory()
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("DELETE FROM sessions")
    finally:
        conn.close()
    assert len(reader.read().snapshot.sessions) == 2


def test_wal_writer_during_read_does_not_tear_tables(tmp_path):
    path = _source(tmp_path)
    def concurrent_writer():
        with sqlite3.connect(path, timeout=2) as writer:
            writer.execute("UPDATE sessions SET updated_at = 5 WHERE id = 's1'")
            writer.execute("INSERT INTO global_memory VALUES (2,'git','Keep WAL',5)")
    source = SqlSnapshotReader.sqlite(path, between_selects=concurrent_writer)
    first = source.read()
    assert len(first.snapshot.facts) == 1
    assert first.snapshot.sessions[0]["updated_at"] == 1
    fresh = SqlSnapshotReader.sqlite(path).read()
    assert len(fresh.snapshot.facts) == 2
    assert fresh.snapshot.sessions[0]["updated_at"] == 5
    assert first.snapshot.digest != fresh.snapshot.digest


def test_owners_are_external_provisional_only(tmp_path):
    reader = SqlSnapshotReader.sqlite(_source(tmp_path))
    audited = reader.read(owner_bindings={"s1": "user1", "s2": "user2"}, known_owner_ids=frozenset({"user1", "user2"}))
    assert audited.owner_audit.status == "externally_asserted_unverified"
    assert len(audited.owner_audit.digest) == 64
    assert "user1" not in audited.owner_audit.digest
    assert audited.snapshot.sessions[0].get("user_id") is None


@pytest.mark.parametrize(
    ("mapping", "owners"),
    [({"s1": "user1"}, frozenset({"user1"})),
     ({"s1": "user1", "s2": "user3"}, frozenset({"user1", "user2"})),
     ({"s1": "user1", "s2": "user2", "extra": "user1"}, frozenset({"user1", "user2"})),
     ({"s1": "user1", "s2": "user2"}, None)],
)
def test_missing_or_untrusted_owner_bindings_fail_closed(tmp_path, mapping, owners):
    with pytest.raises(SnapshotReadError, match="owner"):
        SqlSnapshotReader.sqlite(_source(tmp_path)).read(owner_bindings=mapping, known_owner_ids=owners)


def test_symlinked_sqlite_source_rejected(tmp_path):
    source = _source(tmp_path)
    alias = tmp_path / "alias.sqlite"
    alias.symlink_to(source)
    with pytest.raises(SnapshotReadError, match="non-symlink"):
        SqlSnapshotReader.sqlite(alias)


def test_secret_bearing_sql_data_is_not_staged(tmp_path):
    path = _source(tmp_path)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE sessions SET metadata_json = ? WHERE id = 's1'", ('{"access_token":"secret"}',))
    with pytest.raises(ShadowMismatch, match="secret-store"):
        SqlSnapshotReader.sqlite(path).read()


def test_missing_table_and_config_schema_fail_closed(tmp_path):
    path = tmp_path / "empty.sqlite"
    sqlite3.connect(path).close()
    with pytest.raises(SnapshotReadError, match="read-only SQL snapshot failed"):
        SqlSnapshotReader.sqlite(path).read()


class _FakeCursor:
    def __init__(self):
        self.statements = []
        self.description = None
        self.rows = []
        self.closed = False

    def execute(self, sql):
        self.statements.append(sql)
        if "FROM sessions" in sql:
            self.description = [(name,) for name in ("id", "status", "metadata_json", "created_at", "updated_at", "completed_at")]
            self.rows = [("s1", "active", "{}", 1, 1, None)]
        elif "FROM global_memory" in sql:
            self.description = [(name,) for name in ("id", "category", "fact", "updated_at")]
            self.rows = [(1, "git", "Use file memory", 2)]
        elif "FROM configs" in sql:
            self.description = [("value",)]
            self.rows = [('{"enabled":true}',)]

    def fetchmany(self, size):
        batch = self.rows[:size]
        self.rows = self.rows[size:]
        return batch

    def close(self):
        self.closed = True


class _FakePgConnection:
    def __init__(self, autocommit=True):
        self.autocommit = autocommit
        self.cursor_object = _FakeCursor()
        self.rollback_calls = 0
        self.closed = False

    def cursor(self):
        return self.cursor_object

    def rollback(self):
        self.rollback_calls += 1

    def close(self):
        self.closed = True


def test_postgres_uses_repeatable_read_only_and_rolls_back():
    db = _FakePgConnection()
    reader = SqlSnapshotReader.postgresql(lambda: db)
    snap = reader.read()
    assert len(snap.snapshot.sessions) == 1
    assert db.cursor_object.statements[0] == "BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
    assert db.rollback_calls == 1
    assert db.closed
    assert db.cursor_object.closed
    assert all("UPDATE " not in sql and "INSERT " not in sql for sql in db.cursor_object.statements)


def test_postgres_rejects_non_autocommit_connection_before_any_reads():
    db = _FakePgConnection(False)
    with pytest.raises(SnapshotReadError, match="autocommit"):
        SqlSnapshotReader.postgresql(lambda: db).read()
    assert db.cursor_object.statements == []
    assert db.closed
    assert db.rollback_calls == 1


def test_real_postgres_readonly_repeatable_read_with_concurrent_writer():
    """Runs only in the repository's PostgreSQL CI against an isolated test schema."""
    import os
    import uuid

    dsn = os.environ.get("ALICE_DATABASE_URL")
    if not dsn or not dsn.startswith("postgresql://"):
        pytest.skip("PostgreSQL test service is not configured")
    psycopg = pytest.importorskip("psycopg")
    from psycopg import sql

    name = "shadow_test_" + uuid.uuid4().hex
    ident = sql.Identifier(name)
    with psycopg.connect(dsn, autocommit=True) as setup:
        setup.execute(sql.SQL("CREATE SCHEMA {}").format(ident))
        try:
            setup.execute(
                sql.SQL("CREATE TABLE {}.sessions (id text PRIMARY KEY, status text, metadata_json text, "
                        "created_at integer, updated_at integer, completed_at integer)").format(ident)
            )
            setup.execute(
                sql.SQL("CREATE TABLE {}.global_memory (id integer PRIMARY KEY, category text, "
                        "fact text, updated_at integer)").format(ident)
            )
            setup.execute(
                sql.SQL("CREATE TABLE {}.configs (key text PRIMARY KEY, value text)").format(ident)
            )
            setup.execute(
                sql.SQL("INSERT INTO {}.sessions VALUES (%s,%s,%s,%s,%s,%s)").format(ident),
                ("s1", "active", "{}", 1, 1, None),
            )
            setup.execute(sql.SQL("INSERT INTO {}.global_memory VALUES (1,'git','old',1)").format(ident))
            setup.execute(
                sql.SQL("INSERT INTO {}.configs VALUES (%s,%s)").format(ident),
                ("memory_config", '{"enabled":true}'),
            )

            def connection():
                db = psycopg.connect(dsn, autocommit=True)
                db.execute(sql.SQL("SET search_path TO {}").format(ident))
                return db

            def concurrent_writer():
                with psycopg.connect(dsn, autocommit=True) as writer:
                    writer.execute(sql.SQL("UPDATE {}.sessions SET updated_at=7 WHERE id='s1'").format(ident))
                    writer.execute(sql.SQL("INSERT INTO {}.global_memory VALUES (2,'git','new',7)").format(ident))

            first = SqlSnapshotReader.postgresql(connection, between_selects=concurrent_writer).read()
            second = SqlSnapshotReader.postgresql(connection).read()
            assert len(first.snapshot.facts) == 1
            assert first.snapshot.sessions[0]["updated_at"] == 1
            assert len(second.snapshot.facts) == 2
            assert second.snapshot.sessions[0]["updated_at"] == 7
            assert first.snapshot.digest != second.snapshot.digest
        finally:
            setup.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(ident))

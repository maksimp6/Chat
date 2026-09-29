import pytest

import invocation.manager as invocation_manager
import runtime_migrations
from db_backend import IntegrityError, OperationalError


class _Rows:
    def __init__(self, rows=None):
        self._rows = rows or []
        self.rowcount = 1

    def fetchall(self):
        return self._rows


class _MigrationConnection:
    def __init__(self, *, alter_error=None):
        self.alter_error = alter_error
        self.statements = []
        self.committed = False
        self.closed = False

    def execute(self, sql, params=None):
        normalized = " ".join(sql.split())
        self.statements.append(normalized)
        if normalized.startswith("PRAGMA table_info(sessions)"):
            return _Rows([{"name": "id"}])
        if normalized.startswith("PRAGMA table_info(invocations)"):
            return _Rows([{"name": "id"}])
        if normalized.startswith("ALTER TABLE") and self.alter_error is not None:
            raise self.alter_error
        return _Rows()

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True


def test_runtime_migrations_add_only_missing_columns(monkeypatch):
    conn = _MigrationConnection()
    monkeypatch.setattr(runtime_migrations, "get_conn", lambda: conn)

    runtime_migrations.init_runtime_tables()

    assert any("ALTER TABLE sessions ADD COLUMN completed_at" in sql for sql in conn.statements)
    assert any("ALTER TABLE invocations ADD COLUMN trace_json" in sql for sql in conn.statements)
    assert conn.committed is True
    assert conn.closed is True


def test_runtime_migrations_ignore_duplicate_column_race(monkeypatch):
    conn = _MigrationConnection(alter_error=OperationalError("duplicate column name: completed_at"))
    monkeypatch.setattr(runtime_migrations, "get_conn", lambda: conn)

    runtime_migrations.init_runtime_tables()

    assert conn.closed is True


def test_runtime_migrations_reraise_unexpected_alter_failure(monkeypatch):
    conn = _MigrationConnection(alter_error=OperationalError("database unavailable"))
    monkeypatch.setattr(runtime_migrations, "get_conn", lambda: conn)

    with pytest.raises(OperationalError, match="database unavailable"):
        runtime_migrations.init_runtime_tables()
    assert conn.closed is True


class _InvocationConnection:
    def __init__(self):
        self.committed = False
        self.closed = False

    def execute(self, _sql, _params=None):
        return _Rows()

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True


def test_create_invocation_recovers_when_session_race_is_lost(monkeypatch):
    restored = iter([None, {"id": "session-race", "status": "active"}])
    conn = _InvocationConnection()
    monkeypatch.setattr(invocation_manager, "init_runtime_tables", lambda: None)
    monkeypatch.setattr(invocation_manager, "restore_session", lambda _session_id: next(restored))

    def lose_race(*_args, **_kwargs):
        raise IntegrityError("session already exists")

    monkeypatch.setattr(invocation_manager, "create_session", lose_race)
    monkeypatch.setattr(invocation_manager, "get_conn", lambda: conn)

    context = invocation_manager.create_invocation("session-race", "conversation-race")

    assert context.session_id == "session-race"
    assert context.conversation_id == "conversation-race"
    assert conn.committed is True
    assert conn.closed is True


def test_create_invocation_reraises_when_race_winner_cannot_be_reloaded(monkeypatch):
    monkeypatch.setattr(invocation_manager, "init_runtime_tables", lambda: None)
    monkeypatch.setattr(invocation_manager, "restore_session", lambda _session_id: None)

    def lose_race(*_args, **_kwargs):
        raise IntegrityError("session already exists")

    monkeypatch.setattr(invocation_manager, "create_session", lose_race)

    with pytest.raises(IntegrityError, match="session already exists"):
        invocation_manager.create_invocation("session-race", "conversation-race")

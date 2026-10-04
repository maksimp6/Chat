from pathlib import Path
from types import SimpleNamespace

import pytest

from tests import conftest as isolation


def session(worker=None):
    config = SimpleNamespace()
    if worker is not None:
        config.workerinput = {"workerid": worker}
    return SimpleNamespace(config=config)


def test_workers_get_distinct_databases_before_collection_and_restore_environment(monkeypatch):
    monkeypatch.delenv("ALICE_DATABASE_URL", raising=False)
    monkeypatch.setenv("ALICE_DB_PATH", "original.sqlite")
    first, second = session("gw0"), session("gw1")
    isolation.pytest_sessionstart(first)
    first_path = Path(isolation.os.environ["ALICE_DB_PATH"])
    isolation.pytest_sessionstart(second)
    second_path = Path(isolation.os.environ["ALICE_DB_PATH"])
    assert first_path != second_path
    assert first_path.parent.is_dir() and second_path.parent.is_dir()
    isolation.pytest_sessionfinish(second)
    assert isolation.os.environ["ALICE_DB_PATH"] == str(first_path)
    isolation.pytest_sessionfinish(first)
    assert isolation.os.environ["ALICE_DB_PATH"] == "original.sqlite"
    assert not first_path.parent.exists() and not second_path.parent.exists()


def test_worker_cleanup_unsets_previously_absent_path(monkeypatch):
    monkeypatch.delenv("ALICE_DATABASE_URL", raising=False)
    monkeypatch.delenv("ALICE_DB_PATH", raising=False)
    worker = session("gw0")
    isolation.pytest_sessionstart(worker)
    isolation.pytest_sessionfinish(worker)
    assert "ALICE_DB_PATH" not in isolation.os.environ


def test_serial_suite_leaves_selected_database_unchanged(monkeypatch):
    monkeypatch.setenv("ALICE_DB_PATH", "selected.sqlite")
    serial = session()
    isolation.pytest_sessionstart(serial)
    isolation.pytest_sessionfinish(serial)
    assert isolation.os.environ["ALICE_DB_PATH"] == "selected.sqlite"


def test_postgres_parallelism_is_fail_closed(monkeypatch):
    monkeypatch.setenv("ALICE_DATABASE_URL", "postgresql://example.invalid/disposable")
    with pytest.raises(pytest.UsageError, match="separate databases"):
        isolation.pytest_sessionstart(session("gw0"))

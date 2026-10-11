"""Isolate test Memory DB and xdist SQLite files without PostgreSQL services."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolate_selected_database(monkeypatch, tmp_path):
    monkeypatch.setenv("ALICE_MEMORY_PATH", str(tmp_path / "alice.memory"))
    # Tests never connect to a configured production PostgreSQL database.
    monkeypatch.delenv("ALICE_DATABASE_URL", raising=False)
    yield


def pytest_sessionstart(session):
    # xdist starts fresh interpreters. Select the worker database before collection
    # imports db/app modules, so import-time initialization is isolated too.
    worker = getattr(session.config, "workerinput", None)
    if worker is None:
        return
    directory = tempfile.TemporaryDirectory(prefix="alice-sqlite-" + worker["workerid"] + "-")
    session.config._alice_sqlite_directory = directory
    session.config._alice_original_db_path = os.environ.get("ALICE_DB_PATH")
    os.environ["ALICE_DB_PATH"] = str(Path(directory.name) / "alice_pro.db")


def pytest_sessionfinish(session):
    directory = getattr(session.config, "_alice_sqlite_directory", None)
    if directory is None:
        return
    original = session.config._alice_original_db_path
    if original is None:
        os.environ.pop("ALICE_DB_PATH", None)
    else:
        os.environ["ALICE_DB_PATH"] = original
    directory.cleanup()

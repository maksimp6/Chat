"""Isolated native Kristall writers for serial and xdist application tests."""

from __future__ import annotations
import os
import tempfile
from pathlib import Path
import pytest


@pytest.fixture(autouse=True)
def isolate_selected_database(monkeypatch, tmp_path):
    monkeypatch.setenv("ALICE_MEMORY_PATH", str(tmp_path / "alice.memory"))
    yield
    from agent_memory.client import close_memory_clients

    close_memory_clients()


def pytest_sessionstart(session):
    worker = getattr(session.config, "workerinput", {}) or {}
    name = worker.get("workerid", "serial")
    directory = tempfile.TemporaryDirectory(prefix="alice-memory-" + name + "-")
    session.config._alice_memory_directory = directory
    session.config._alice_original_memory_path = os.environ.get("ALICE_MEMORY_PATH")
    os.environ["ALICE_MEMORY_PATH"] = str(Path(directory.name) / "alice.memory")


def pytest_sessionfinish(session):
    from agent_memory.client import close_memory_clients

    close_memory_clients()
    directory = getattr(session.config, "_alice_memory_directory", None)
    if directory is None:
        return
    original = session.config._alice_original_memory_path
    if original is None:
        os.environ.pop("ALICE_MEMORY_PATH", None)
    else:
        os.environ["ALICE_MEMORY_PATH"] = original
    directory.cleanup()

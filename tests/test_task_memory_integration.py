"""Logos task/client acceptance through the standalone Kristall API."""

import json
import subprocess
import sys

import pytest

from agent_memory.client import close_memory_clients, get_memory_client
from agent_shell.store import TaskStore
from kristall import KristallError


@pytest.fixture(autouse=True)
def close_clients():
    yield
    close_memory_clients()


def test_aliases_share_one_application_writer(tmp_path):
    path = tmp_path / "shell.memory"
    assert get_memory_client(path) is get_memory_client(tmp_path / "." / "shell.memory")
    first = TaskStore(path)
    second = TaskStore(path)
    task_id = first.add(role="docs-engineer", title="saved", kind="echo")
    assert second.get(task_id)["title"] == "saved"


def test_failed_claim_is_not_published_or_acknowledged(tmp_path, monkeypatch):
    import kristall.store as engine

    path = tmp_path / "shell.memory"
    tasks = TaskStore(path)
    task_id = tasks.add(role="docs-engineer", title="pending", kind="echo")

    def fail(_fd):
        raise OSError(28, "disk full")

    monkeypatch.setattr(engine.os, "fsync", fail)
    with pytest.raises(KristallError, match="commit failed"):
        tasks.claim_next()
    assert tasks.get(task_id)["status"] == "queued"
    assert [event["stage"] for event in tasks.events(task_id)] == ["queued"]
    with pytest.raises(KristallError, match="recovery required"):
        tasks.claim_next()


def test_fresh_process_reopens_tasks_and_preserves_profile_payload(tmp_path):
    path = tmp_path / "shell.memory"
    store = TaskStore(path)
    task_id = store.add(
        role="docs-engineer", title="saved", kind="echo", payload={"profile": {"model": "local"}}
    )
    store.claim_next()
    close_memory_clients(path)
    script = """
import json, sys
from agent_shell.store import TaskStore
from agent_memory.client import close_memory_clients
store = TaskStore(sys.argv[1])
task_id = int(sys.argv[2])
assert store.get(task_id)["payload"] == {"profile": {"model": "local"}}
assert store.recover_interrupted() == [task_id]
assert store.claim_next() is None
print(json.dumps(store.get(task_id)))
close_memory_clients()
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(path), str(task_id)],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["error"] == "interrupted"
    reopened = TaskStore(path)
    assert reopened.get(task_id)["status"] == "failed"
    assert reopened.events(task_id)[-1]["stage"] == "interrupted"


def test_legacy_database_is_not_overwritten_or_silently_retired(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    original = b"SQLite format 3\x00legacy source"
    path.write_bytes(original)
    with pytest.raises(KristallError):
        TaskStore(path)
    assert path.read_bytes() == original


def test_task_record_and_events_are_copy_safe(tmp_path):
    tasks = TaskStore(tmp_path / "shell.memory")
    task_id = tasks.add(role="docs-engineer", title="safe", kind="echo", payload={"items": [1]})
    tasks.get(task_id)["payload"]["items"].append(2)
    tasks.events(task_id).clear()
    assert tasks.get(task_id)["payload"] == {"items": [1]}
    assert len(tasks.events(task_id)) == 1


@pytest.mark.parametrize("fault", ["state", "task", "ownership", "events"])
def test_invalid_repository_state_fails_closed(tmp_path, fault):
    from agent_shell.store import _STATE_KEY

    path = tmp_path / "shell.memory"
    tasks = TaskStore(path)
    task_id = tasks.add(role="docs-engineer", title="safe", kind="echo")
    client = get_memory_client(path)
    state = client.get(_STATE_KEY)
    if fault == "state":
        state["schema"] = 2
    elif fault == "task":
        state["tasks"][str(task_id)]["id"] = 0
    elif fault == "ownership":
        state["events"] = {}
    else:
        state["events"][str(task_id)] = [{"stage": "queued", "at": "invalid"}]
    client.set(_STATE_KEY, state)
    client.commit()
    sequence = client.last_commit.sequence
    with pytest.raises(KristallError, match="invalid task"):
        tasks.claim_next()
    assert client.last_commit.sequence == sequence


def test_finishing_missing_task_does_not_create_orphan_event(tmp_path):
    path = tmp_path / "shell.memory"
    tasks = TaskStore(path)
    with pytest.raises(ValueError, match="not found"):
        tasks.finish(999, {})
    assert tasks.list() == []
    assert tasks.events(999) == []
    assert get_memory_client(path).last_commit.sequence == 0

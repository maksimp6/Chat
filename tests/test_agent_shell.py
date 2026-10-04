"""Agent shell: task store, runner gates, safe errors, and the first read-only task."""

import json
import sqlite3
import threading

import pytest

from agent_shell import __main__ as cli
from agent_shell import handlers as builtin
from agent_shell.runner import run_next
from agent_shell.store import TaskStore


@pytest.fixture
def store(tmp_path):
    return TaskStore(tmp_path / "shell.sqlite3")


def ok_handler(payload):
    return {"echo": payload.get("text")}


def test_tasks_are_stored_listed_and_run_in_fifo_order(store):
    first = store.add(role="infra-engineer", title="one", kind="echo", payload={"text": "a"})
    second = store.add(role="infra-engineer", title="two", kind="echo", payload={"text": "b"})
    assert first != second
    assert [t["title"] for t in store.list()] == ["one", "two"]
    assert run_next(store, {"echo": ok_handler})["id"] == first
    assert run_next(store, {"echo": ok_handler})["id"] == second
    assert run_next(store, {"echo": ok_handler}) is None


def test_a_finished_task_keeps_its_result_and_a_trace_of_events(store):
    task_id = store.add(role="infra-engineer", title="t", kind="echo", payload={"text": "hi"})
    run_next(store, {"echo": ok_handler})
    task = store.get(task_id)
    assert task["status"] == "done"
    assert task["result"] == {"echo": "hi"}
    assert [e["stage"] for e in store.events(task_id)] == ["queued", "started", "finished"]


def test_failures_store_only_a_fixed_code_never_the_exception_text(store):
    def boom(payload):
        raise RuntimeError("Authorization: Bearer leaked-token")

    task_id = store.add(role="infra-engineer", title="t", kind="boom")
    run_next(store, {"boom": boom})
    task = store.get(task_id)
    assert task["status"] == "failed"
    assert task["error"] == "task_failed"
    assert "leaked-token" not in json.dumps([task, store.events(task_id)])


def test_unknown_kind_fails_without_running_anything(store):
    task_id = store.add(role="infra-engineer", title="t", kind="rm_rf")
    ran = []
    run_next(store, {"echo": lambda p: ran.append(1)})
    assert store.get(task_id)["error"] == "unknown_kind"
    assert ran == []


def test_a_result_that_is_not_json_fails_the_task(store):
    task_id = store.add(role="infra-engineer", title="t", kind="bad")
    run_next(store, {"bad": lambda p: {"x": object()}})
    assert store.get(task_id)["status"] == "failed"
    assert store.get(task_id)["error"] == "invalid_result"


def test_dangerous_tasks_wait_for_owner_approval_and_are_never_picked_before(store):
    task_id = store.add(role="release-manager", title="deploy", kind="echo", needs_approval=True)
    safe_id = store.add(role="infra-engineer", title="safe", kind="echo")
    ran = run_next(store, {"echo": ok_handler})
    assert ran["id"] == safe_id
    assert store.get(task_id)["status"] == "blocked"
    assert run_next(store, {"echo": ok_handler}) is None
    store.approve(task_id)
    assert store.get(task_id)["status"] == "queued"
    assert run_next(store, {"echo": ok_handler})["id"] == task_id
    assert [e["stage"] for e in store.events(task_id)][:3] == ["queued", "blocked", "approved"]


def test_approving_an_unknown_or_non_blocked_task_is_refused(store):
    task_id = store.add(role="infra-engineer", title="t", kind="echo")
    with pytest.raises(ValueError):
        store.approve(task_id)
    with pytest.raises(ValueError):
        store.approve(999)


def test_two_runners_never_claim_the_same_task(tmp_path):
    path = tmp_path / "shell.sqlite3"
    TaskStore(path).add(role="infra-engineer", title="t", kind="echo")
    claimed = []
    barrier = threading.Barrier(4)

    def worker():
        mine = TaskStore(path)
        barrier.wait()
        claimed.append(mine.claim_next())

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sum(1 for item in claimed if item) == 1


def test_a_task_left_running_by_a_crash_is_failed_not_rerun(store):
    task_id = store.add(role="infra-engineer", title="t", kind="echo")
    store.claim_next()
    assert store.recover_interrupted() == [task_id]
    assert store.get(task_id)["status"] == "failed"
    assert store.get(task_id)["error"] == "interrupted"


def test_fields_are_validated_and_stored_as_data_not_executed(store):
    with pytest.raises(ValueError):
        store.add(role="not a role!", title="t", kind="echo")
    with pytest.raises(ValueError):
        store.add(role="infra-engineer", title="", kind="echo")
    with pytest.raises(ValueError):
        store.add(role="infra-engineer", title="x" * 300, kind="echo")
    task_id = store.add(role="infra-engineer", title="'; DROP TABLE tasks; --", kind="echo")
    assert store.get(task_id)["title"] == "'; DROP TABLE tasks; --"
    assert sqlite3.connect(store.path).execute("select count(*) from tasks").fetchone()[0] == 1


def test_first_task_reads_cloudru_status_through_the_read_only_check():
    calls = []

    def fake_run(names, env=None, factories=None):
        calls.append(names)
        return {"containers": {"ok": True, "data": [{"name": "chrome-test"}]}}

    handlers = builtin.default_handlers(check_run=fake_run)
    assert set(handlers) == {"cloudru_status"}
    result = handlers["cloudru_status"]({})
    assert calls == [["containers", "registries"]]
    assert result["containers"]["data"][0]["name"] == "chrome-test"


def test_cli_adds_runs_and_shows_a_task(tmp_path, capsys):
    db = str(tmp_path / "shell.sqlite3")
    handlers = {"echo": ok_handler}
    assert (
        cli.main(
            ["--db", db, "add", "--role", "infra-engineer", "--title", "hello", "--kind", "echo"],
            handlers=handlers,
        )
        == 0
    )
    task_id = int(capsys.readouterr().out.strip())
    assert cli.main(["--db", db, "run-next"], handlers=handlers) == 0
    assert "done" in capsys.readouterr().out
    assert cli.main(["--db", db, "show", str(task_id)], handlers=handlers) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["status"] == "done" and [e["stage"] for e in shown["events"]][-1] == "finished"
    assert cli.main(["--db", db, "show", "404"], handlers=handlers) == 1

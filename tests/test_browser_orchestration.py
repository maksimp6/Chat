import threading
import time

import pytest

from browser.orchestration import (
    BrowserDagBudget,
    BrowserRoleDag,
    BrowserRoleTask,
    BrowserTaskBudget,
)


def task(
    task_id,
    *,
    role="observer",
    session_id="session-a",
    action="inspect",
    dependencies=(),
    budget=None,
):
    return BrowserRoleTask(
        task_id=task_id,
        role=role,
        session_id=session_id,
        capability="browser_cloud",
        action=action,
        target="page",
        dependencies=tuple(dependencies),
        budget=budget or BrowserTaskBudget(),
    )


def test_parallel_reads_can_run_in_same_session():
    lock = threading.Lock()
    both_started = threading.Event()
    release = threading.Event()
    active = 0
    max_active = 0
    holder = {}

    def worker(_task):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
            if active >= 2:
                both_started.set()
        release.wait(timeout=2)
        with lock:
            active -= 1
        return {"success": True, "data": {"ok": True}}

    dag = BrowserRoleDag(
        [task("read-a"), task("read-b")],
        worker,
        budget=BrowserDagBudget(max_parallel_workers=2),
    )

    thread = threading.Thread(target=lambda: holder.setdefault("result", dag.run()))
    thread.start()
    assert both_started.wait(timeout=2)
    release.set()
    thread.join(timeout=2)

    assert thread.is_alive() is False
    assert max_active == 2
    assert holder["result"].status == "succeeded"


def test_writes_are_serialized_within_one_session():
    lock = threading.Lock()
    active = 0
    max_active = 0

    def worker(_task):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.02)
        with lock:
            active -= 1
        return {"success": True, "data": {"ok": True}}

    result = BrowserRoleDag(
        [
            task("click-a", role="mouse", action="click"),
            task("click-b", role="mouse", action="click"),
        ],
        worker,
        budget=BrowserDagBudget(max_parallel_workers=2),
    ).run()

    assert result.status == "succeeded"
    assert max_active == 1


def test_write_does_not_overlap_read_in_same_session():
    lock = threading.Lock()
    active = 0
    max_active = 0

    def worker(_task):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.02)
        with lock:
            active -= 1
        return {"success": True, "data": {"ok": True}}

    result = BrowserRoleDag(
        [
            task("a-write", role="keyboard", action="fill"),
            task("b-read", role="observer", action="inspect"),
        ],
        worker,
        budget=BrowserDagBudget(max_parallel_workers=2),
    ).run()

    assert result.status == "succeeded"
    assert max_active == 1


def test_writes_in_different_sessions_can_run_in_parallel():
    lock = threading.Lock()
    both_started = threading.Event()
    release = threading.Event()
    active = 0
    max_active = 0
    holder = {}

    def worker(_task):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
            if active >= 2:
                both_started.set()
        release.wait(timeout=2)
        with lock:
            active -= 1
        return {"success": True, "data": {"ok": True}}

    dag = BrowserRoleDag(
        [
            task("write-a", role="mouse", action="click", session_id="a"),
            task("write-b", role="mouse", action="click", session_id="b"),
        ],
        worker,
        budget=BrowserDagBudget(max_parallel_workers=2),
    )
    thread = threading.Thread(target=lambda: holder.setdefault("result", dag.run()))
    thread.start()
    assert both_started.wait(timeout=2)
    release.set()
    thread.join(timeout=2)

    assert max_active == 2
    assert holder["result"].status == "succeeded"


def test_dependencies_and_failure_propagation_are_fail_closed():
    calls = []

    def worker(item):
        calls.append(item.task_id)
        if item.task_id == "root":
            raise RuntimeError("boom")
        return {"success": True, "data": item.task_id}

    result = BrowserRoleDag(
        [
            task("root"),
            task("child", dependencies=("root",)),
            task("grandchild", dependencies=("child",)),
            task("independent", session_id="other"),
        ],
        worker,
        budget=BrowserDagBudget(max_parallel_workers=2),
    ).run()

    assert result.status == "failed"
    assert result.tasks["root"].status == "failed"
    assert "RuntimeError: boom" in result.tasks["root"].error
    assert result.tasks["child"].status == "blocked"
    assert result.tasks["grandchild"].status == "blocked"
    assert result.tasks["independent"].status == "succeeded"
    assert "child" not in calls
    assert "grandchild" not in calls


def test_task_token_and_time_budgets_fail_closed():
    token_result = BrowserRoleDag(
        [
            task(
                "token-heavy",
                budget=BrowserTaskBudget(max_tokens=5),
            )
        ],
        lambda _task: {
            "success": True,
            "data": {"ok": True},
            "metadata": {"consumed_tokens": 6},
        },
    ).run()

    assert token_result.status == "failed"
    assert token_result.tasks["token-heavy"].error == "task token budget exceeded"
    assert token_result.tasks["token-heavy"].consumed_tokens == 6

    time_result = BrowserRoleDag(
        [
            task(
                "slow",
                budget=BrowserTaskBudget(max_seconds=0.001),
            )
        ],
        lambda _task: time.sleep(0.01) or {"success": True, "data": "done"},
    ).run()

    assert time_result.status == "failed"
    assert time_result.tasks["slow"].error == "task time budget exceeded"


def test_global_token_budget_cancels_pending_tasks():
    result = BrowserRoleDag(
        [task("a"), task("b"), task("c")],
        lambda _task: {
            "success": True,
            "data": {"ok": True},
            "usage": {"total_tokens": 6},
        },
        budget=BrowserDagBudget(max_parallel_workers=1, max_total_tokens=5),
    ).run()

    assert result.status == "failed"
    assert result.consumed_tokens == 6
    assert result.tasks["a"].status == "failed"
    assert result.tasks["a"].error == "DAG token budget exceeded"
    assert result.tasks["b"].status == "cancelled"
    assert result.tasks["c"].status == "cancelled"


def test_large_worker_output_is_compacted():
    result = BrowserRoleDag(
        [
            task(
                "large",
                budget=BrowserTaskBudget(max_output_chars=20),
            )
        ],
        lambda _task: {"success": True, "data": {"text": "x" * 200}},
    ).run()

    task_result = result.tasks["large"]
    assert task_result.status == "succeeded"
    assert task_result.output_chars == 20
    assert task_result.data["truncated"] is True
    assert task_result.data["original_chars"] > 20
    assert len(task_result.data["preview"]) == 20


def test_mapping_worker_error_becomes_failed_task():
    result = BrowserRoleDag(
        [task("bad")],
        lambda _task: {"success": False, "error": "adapter unavailable"},
    ).run()

    assert result.status == "failed"
    assert result.tasks["bad"].status == "failed"
    assert "adapter unavailable" in result.tasks["bad"].error


def test_result_mapping_and_empty_dag():
    result = BrowserRoleDag([], lambda _task: None).run()

    assert result.status == "succeeded"
    assert result.tasks == {}
    assert result.to_mapping()["tasks"] == {}


def test_dag_validation_rejects_invalid_graphs_and_budgets():
    with pytest.raises(TypeError, match="worker must be callable"):
        BrowserRoleDag([], None)

    with pytest.raises(ValueError, match="duplicate task_id"):
        BrowserRoleDag([task("dup"), task("dup")], lambda _task: None)

    with pytest.raises(ValueError, match="missing dependencies"):
        BrowserRoleDag([task("child", dependencies=("missing",))], lambda _task: None)

    with pytest.raises(ValueError, match="cycle"):
        BrowserRoleDag(
            [
                task("a", dependencies=("b",)),
                task("b", dependencies=("a",)),
            ],
            lambda _task: None,
        )

    with pytest.raises(ValueError, match="task count exceeds"):
        BrowserRoleDag(
            [task("a"), task("b")],
            lambda _task: None,
            budget=BrowserDagBudget(max_total_tasks=1),
        )

    with pytest.raises(ValueError, match="max_parallel_workers"):
        BrowserDagBudget(max_parallel_workers=0)
    with pytest.raises(ValueError, match="max_total_tasks"):
        BrowserDagBudget(max_total_tasks=0)
    with pytest.raises(ValueError, match="max_total_tokens"):
        BrowserDagBudget(max_total_tokens=-1)

    with pytest.raises(ValueError, match="max_seconds"):
        BrowserTaskBudget(max_seconds=0)
    with pytest.raises(ValueError, match="max_tokens"):
        BrowserTaskBudget(max_tokens=-1)
    with pytest.raises(ValueError, match="max_output_chars"):
        BrowserTaskBudget(max_output_chars=0)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"task_id": ""}, "task_id is required"),
        ({"role": "planner"}, "unsupported browser role"),
        ({"session_id": ""}, "session_id is required"),
        ({"action": "dance"}, "unsupported browser action"),
        ({"role": "mouse", "action": "inspect"}, "cannot execute action"),
        ({"target": ""}, "target is required"),
        ({"dependencies": ("same",), "task_id": "same"}, "depend on itself"),
    ],
)
def test_task_validation(kwargs, message):
    values = {
        "task_id": "task",
        "role": "observer",
        "session_id": "session",
        "capability": "browser_cloud",
        "action": "inspect",
        "target": "page",
    }
    values.update(kwargs)

    with pytest.raises(ValueError, match=message):
        BrowserRoleTask(**values)

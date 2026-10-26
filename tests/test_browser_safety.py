import threading
import time

from browser import capabilities, orchestration, pipeline


def test_secret_inputs_are_redacted_from_inspection_results():
    value = capabilities.sanitize_browser_value(
        {"inputs": [{"name": "access_token", "type": "hidden", "value": "SECRET-VALUE"}]}
    )
    assert "SECRET-VALUE" not in str(value)


def test_navigate_counts_as_a_write_for_session_scheduling():
    assert "navigate" in orchestration.WRITE_ACTIONS


def test_truncated_observation_is_kept_as_a_truncated_snapshot():
    snapshot = pipeline._extract_snapshot(
        {"truncated": True, "preview": "partial", "original_chars": 99999}
    )
    assert snapshot is not None
    assert snapshot.truncated is True


def test_timed_out_write_blocks_session_without_waiting_for_worker():
    release = threading.Event()
    executed = []

    def worker(task):
        executed.append(task.task_id)
        release.wait(timeout=2)
        return {"success": True, "data": {}}

    tasks = [
        orchestration.BrowserRoleTask(
            "a-write", "mouse", "session", "browser_cloud", "click", "button",
            budget=orchestration.BrowserTaskBudget(max_seconds=0.01),
        ),
        orchestration.BrowserRoleTask(
            "b-read", "observer", "session", "browser_cloud", "inspect", "page",
        ),
    ]
    started = time.monotonic()
    try:
        result = orchestration.BrowserRoleDag(
            tasks, worker, budget=orchestration.BrowserDagBudget(max_parallel_workers=1)
        ).run()
        assert time.monotonic() - started < 1
        assert result.status == "failed"
        assert result.tasks["a-write"].status == "failed"
        assert result.tasks["b-read"].status == "blocked"
        assert executed == ["a-write"]
    finally:
        release.set()

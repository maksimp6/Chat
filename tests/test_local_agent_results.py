"""Real SQLite result-waiter tests; no browser, network, Flask or paid agent."""

import json
import sqlite3

import pytest

from local_agents import results


@pytest.fixture()
def queue(tmp_path):
    path = tmp_path / "jobs.db"

    def connect():
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        return conn

    conn = connect()
    conn.execute(
        "CREATE TABLE local_agent_jobs (id TEXT PRIMARY KEY, agent_id TEXT, "
        "trace_id TEXT, invocation_id TEXT, status TEXT, result_json TEXT)"
    )
    conn.execute(
        "INSERT INTO local_agent_jobs VALUES (?, ?, ?, ?, ?, ?)",
        ("job-1", "laptop-1", "trace-1", "invocation-1", "queued", None),
    )
    conn.commit()
    conn.close()
    return connect


def finish(queue, payload, status="completed"):
    conn = queue()
    try:
        conn.execute(
            "UPDATE local_agent_jobs SET status = ?, result_json = ? WHERE id = ?",
            (status, payload, "job-1"),
        )
        conn.commit()
    finally:
        conn.close()


@pytest.mark.parametrize("data", [{"title": "Test page"}, [1, 2], "done", 0, None])
def test_completed_plain_json(queue, data):
    finish(queue, json.dumps(data))
    response = results.wait_for_job_result(queue, "job-1", timeout_seconds=0)
    assert response["success"] is True
    assert response["data"] == data
    assert response["metadata"] == {
        "job_id": "job-1",
        "agent_id": "laptop-1",
        "trace_id": "trace-1",
        "invocation_id": "invocation-1",
        "job_status": "completed",
    }


def test_completed_tool_envelope_preserves_data_not_remote_metadata(queue):
    finish(
        queue,
        json.dumps(
            {
                "success": True,
                "data": {"title": "Test page"},
                "error": "secret-error-text",
                "metadata": {"trace_id": "forged", "authorization": "secret-value"},
            }
        ),
    )
    response = results.wait_for_job_result(queue, "job-1", timeout_seconds=0)
    assert response["success"] is True
    assert response["data"] == {"title": "Test page"}
    assert response["metadata"]["trace_id"] == "trace-1"
    assert "secret" not in json.dumps(response)


def test_envelope_without_data_matches_universal_result_contract(queue):
    finish(queue, '{"success":true,"answer":42}')
    response = results.wait_for_job_result(queue, "job-1", timeout_seconds=0)
    assert response["data"] == {"answer": 42}


@pytest.mark.parametrize(
    ("status", "payload", "error"),
    [
        ("failed", '{"success":true,"password":"hidden"}', "local_agent_job_failed"),
        ("completed", '{"success":false,"error":"hidden"}', "local_agent_tool_failed"),
        ("completed", '{"error":"hidden"}', "local_agent_tool_failed"),
        ("unknown", "{}", "local_agent_job_invalid_status"),
    ],
)
def test_failure_never_becomes_success_or_echoes_remote_error(queue, status, payload, error):
    finish(queue, payload, status)
    response = results.wait_for_job_result(queue, "job-1", timeout_seconds=0)
    assert response["success"] is False
    assert response["data"] is None
    assert response["error"] == error
    assert "hidden" not in json.dumps(response)


@pytest.mark.parametrize(
    "payload",
    [
        None,
        "",
        "{",
        "NaN",
        "Infinity",
        "1e999",
        '{"success":"false"}',
        '{"success":1}',
        '{"success":null}',
        "[" * 1100 + "]" * 1100,
        " " * (results._MAX_RESULT_CHARACTERS + 1),
    ],
    ids=[
        "missing",
        "empty",
        "broken",
        "nan",
        "infinity",
        "overflow",
        "string-success",
        "integer-success",
        "null-success",
        "deep",
        "oversized",
    ],
)
def test_corrupt_or_oversized_result_fails_closed(queue, payload):
    finish(queue, payload)
    response = results.wait_for_job_result(queue, "job-1", timeout_seconds=0)
    assert response["success"] is False
    assert response["error"] == "local_agent_result_invalid"


@pytest.mark.parametrize("status", ["queued", "running"])
def test_zero_timeout_does_not_change_pending_job(queue, status):
    finish(queue, None, status)
    assert results.wait_for_job_result(queue, "job-1", timeout_seconds=0) is None
    conn = queue()
    try:
        assert conn.execute("SELECT status FROM local_agent_jobs").fetchone()[0] == status
        assert conn.execute("SELECT COUNT(*) FROM local_agent_jobs").fetchone()[0] == 1
    finally:
        conn.close()


@pytest.mark.parametrize(
    "timeout", [-1, float("nan"), float("inf"), 301, True, "1", None, 10**1000]
)
def test_invalid_timeout_is_rejected_before_database_access(timeout):
    def forbidden():
        pytest.fail("invalid wait settings must not access the database")

    with pytest.raises(ValueError, match="timeout"):
        results.wait_for_job_result(forbidden, "job-1", timeout_seconds=timeout)


@pytest.mark.parametrize("job_id", [None, "", "  ", "x" * 129])
def test_invalid_job_id_is_rejected(queue, job_id):
    with pytest.raises(ValueError, match="job id"):
        results.wait_for_job_result(queue, job_id)


def test_missing_job_has_explicit_error(queue):
    with pytest.raises(LookupError, match="local_agent_job_not_found"):
        results.wait_for_job_result(queue, "missing", timeout_seconds=0)


def test_polling_uses_monotonic_deadline_and_closes_connections(queue, monkeypatch):
    current = [100.0]
    sleeps = []
    opened = []

    def connect():
        conn = queue()
        opened.append(conn)
        return conn

    def sleep(seconds):
        assert 0 < seconds <= results._POLL_SECONDS
        for conn in opened:
            with pytest.raises(sqlite3.ProgrammingError, match="closed"):
                conn.execute("SELECT 1")
        sleeps.append(seconds)
        current[0] += seconds

    monkeypatch.setattr(results.time, "monotonic", lambda: current[0])
    monkeypatch.setattr(results.time, "sleep", sleep)
    monkeypatch.setattr(
        results.time, "time", lambda: pytest.fail("wall clock must not set deadline")
    )
    assert results.wait_for_job_result(connect, "job-1", timeout_seconds=0.12) is None
    assert sum(sleeps) == pytest.approx(0.12)
    assert len(sleeps) == 3


def test_completion_between_polls_is_returned_without_real_sleep(queue, monkeypatch):
    sleeps = []

    def complete(seconds):
        sleeps.append(seconds)
        finish(queue, '{"success":true,"data":{"answer":42}}')

    monkeypatch.setattr(results.time, "sleep", complete)
    response = results.wait_for_job_result(queue, "job-1")
    assert response["data"] == {"answer": 42}
    assert sleeps == [results._POLL_SECONDS]


def test_database_error_still_closes_connection():
    class BrokenConnection:
        closed = False

        def execute(self, *_args):
            raise RuntimeError("database unavailable")

        def close(self):
            self.closed = True

    conn = BrokenConnection()
    with pytest.raises(RuntimeError, match="database unavailable"):
        results.wait_for_job_result(lambda: conn, "job-1")
    assert conn.closed


def test_json_depth_ignores_brackets_inside_escaped_strings(queue):
    data = {"text": "[" * 100 + '\\"' + "]" * 100, "number": 1.25}
    finish(queue, json.dumps(data))
    response = results.wait_for_job_result(queue, "job-1", timeout_seconds=0)
    assert response["data"] == data

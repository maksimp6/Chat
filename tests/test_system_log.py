import json
import logging

import pytest

from observability import system_log
from observability.trace_manager import ExecutionTrace, bind_current_trace, reset_current_trace


@pytest.fixture
def configured(tmp_path):
    root = logging.getLogger(system_log.ROOT_LOGGER_NAME)
    saved = (list(root.handlers), getattr(root, "_alice_configured", False))
    for handler in list(root.handlers):
        root.removeHandler(handler)
    root._alice_configured = False
    system_log.configure("DEBUG", directory=tmp_path)
    yield tmp_path / system_log.DEFAULT_LOG_FILE
    for handler in list(root.handlers):
        handler.close()
        root.removeHandler(handler)
    for handler in saved[0]:
        root.addHandler(handler)
    root._alice_configured = saved[1]


def _records(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_levels_are_preserved(configured):
    log = system_log.get_logger("test")
    log.info("started")
    log.error("failed")
    levels = {r["message"]: r["level"] for r in _records(configured)}
    assert levels == {"started": "INFO", "failed": "ERROR"}


def test_records_carry_trace_ids_and_warnings_mirror_into_trace(configured):
    trace = ExecutionTrace()
    token = bind_current_trace(trace)
    try:
        system_log.get_logger("test").warning("slow upstream")
    finally:
        reset_current_trace(token)
    record = _records(configured)[-1]
    assert record["trace_id"] == trace.trace_id
    assert record["invocation_id"]
    events = trace.trace["events"]
    assert any(
        e["type"] == "system_log" and e["payload"]["message"] == "slow upstream" for e in events
    )


def test_no_trace_means_null_ids(configured):
    system_log.get_logger("test").info("boot")
    assert _records(configured)[-1]["trace_id"] is None

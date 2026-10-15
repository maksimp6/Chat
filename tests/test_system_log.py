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


def test_mirror_skips_reentrant_emit(configured):
    trace = ExecutionTrace()
    token = bind_current_trace(trace)
    system_log._mirroring.active = True
    try:
        system_log.get_logger("test").warning("nested")
    finally:
        system_log._mirroring.active = False
        reset_current_trace(token)
    assert not any(e["type"] == "system_log" for e in trace.trace["events"])


def test_mirror_failure_never_breaks_logging(configured, monkeypatch):
    trace = ExecutionTrace()

    def boom(*args, **kwargs):
        raise RuntimeError("trace unavailable")

    monkeypatch.setattr(trace, "add_event", boom)
    token = bind_current_trace(trace)
    try:
        system_log.get_logger("test").error("still logged")
    finally:
        reset_current_trace(token)
    assert _records(configured)[-1]["message"] == "still logged"
    assert system_log._mirroring.active is False


def test_unwritable_log_dir_falls_back_to_console(tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    root = logging.getLogger(system_log.ROOT_LOGGER_NAME)
    saved = (list(root.handlers), getattr(root, "_alice_configured", False))
    for handler in list(root.handlers):
        root.removeHandler(handler)
    root._alice_configured = False
    try:
        system_log.configure("INFO", directory=blocker)
        assert not any(h.__class__.__name__ == "RotatingFileHandler" for h in root.handlers)
    finally:
        for handler in list(root.handlers):
            handler.close()
            root.removeHandler(handler)
        for handler in saved[0]:
            root.addHandler(handler)
        root._alice_configured = saved[1]

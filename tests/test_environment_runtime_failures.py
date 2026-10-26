import queue
import threading
import time

import pytest

import remote_runtime.environments as environment_manager
import remote_runtime.environment_routes as environment_routes
from remote_runtime.environments import (
    EnvironmentRuntime,
    RuntimeHTTPStream,
    _RUNTIME_DISPATCHER,
    _RUNTIME_WORKERS,
    _RUNTIME_WORKERS_LOCK,
    _stream_put,
    dispatch_environment_http,
    dispatch_environment_operation,
    register_runtime_operation,
)
from runtime import RuntimeNotFound


def _environment(tmp_path, runtime_id):
    return {
        "environment_id": runtime_id,
        "owner_id": "owner-a",
        "data_namespace": "env_" + runtime_id.replace("-", "_"),
        "commit_sha": "0" * 40,
    }


def _runtime(tmp_path, runtime_id):
    runtime = EnvironmentRuntime(_environment(tmp_path, runtime_id))
    runtime._prepare = lambda: None
    return runtime


def _restore_http_handler():
    register_runtime_operation("http.request", environment_routes._runtime_http_request)


def test_runtime_http_stream_propagates_worker_error_and_closes():
    events = queue.Queue()
    cancel = threading.Event()
    events.put(("error", ValueError("stream failed")))
    stream = RuntimeHTTPStream(200, [], events, cancel)

    with pytest.raises(ValueError, match="stream failed"):
        list(stream)

    assert cancel.is_set()


def test_stream_put_stops_after_backpressure_is_cancelled():
    cancel = threading.Event()

    class FullQueue:
        def __init__(self):
            self.calls = 0

        def put(self, event, timeout):
            self.calls += 1
            if self.calls == 2:
                cancel.set()
            raise queue.Full

    events = FullQueue()
    assert _stream_put(events, ("chunk", b"x"), cancel) is False
    assert events.calls == 2


def test_runtime_dispatch_success_error_timeout_and_duplicate_start(tmp_path):
    runtime = _runtime(tmp_path, "runtime-generic")
    release = threading.Event()

    def echo(context, payload):
        return {"runtime_id": context.runtime_id, "value": payload.get("value")}

    def fail(context, payload):
        raise ValueError("operation failed")

    def block(context, payload):
        release.wait(1)
        return "released"

    register_runtime_operation("test.echo", echo)
    register_runtime_operation("test.fail", fail)
    register_runtime_operation("test.block", block)
    runtime.start()

    try:
        assert dispatch_environment_operation(runtime.runtime_id, "test.echo", {"value": 7}) == {
            "runtime_id": runtime.runtime_id,
            "value": 7,
        }

        with pytest.raises(ValueError, match="operation failed"):
            dispatch_environment_operation(runtime.runtime_id, "test.fail")

        with pytest.raises(TimeoutError, match="operation timed out"):
            dispatch_environment_operation(runtime.runtime_id, "test.block", timeout=0.001)
        release.set()
        assert (
            dispatch_environment_operation(runtime.runtime_id, "test.echo", {"value": 8})["value"]
            == 8
        )

        duplicate = _runtime(tmp_path, runtime.runtime_id)
        with pytest.raises(RuntimeError, match="already running"):
            duplicate.start()

        assert (
            dispatch_environment_operation(runtime.runtime_id, "test.echo", {"value": 9})[
                "runtime_id"
            ]
            == runtime.runtime_id
        )
    finally:
        release.set()
        runtime.stop()


def test_runtime_start_failure_cleans_registry_and_dispatcher(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path, "runtime-start-failure")

    class NeverStarts:
        ident = None

        def start(self):
            return None

    runtime._thread = NeverStarts()
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)

    with pytest.raises(RuntimeError, match="did not start"):
        runtime.start()

    with _RUNTIME_WORKERS_LOCK:
        assert runtime.runtime_id not in _RUNTIME_WORKERS
    with pytest.raises(RuntimeNotFound):
        _RUNTIME_DISPATCHER.context(runtime.runtime_id)


def test_runtime_rejects_submission_before_start(tmp_path):
    runtime = _runtime(tmp_path, "runtime-not-started")

    with pytest.raises(RuntimeError, match="not running"):
        runtime.submit("test.echo")
    with pytest.raises(RuntimeError, match="not running"):
        runtime.submit_http({})


def test_runtime_http_submission_timeout_and_invalid_start(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path, "runtime-http-submission")
    entered = threading.Event()
    release = threading.Event()
    closed = threading.Event()

    def blocked_http(context, payload):
        entered.set()
        release.wait()
        return {"status_code": 200, "headers": [], "body": (), "close": closed.set}

    register_runtime_operation("http.request", blocked_http)
    runtime.start()
    try:
        with pytest.raises(TimeoutError, match="HTTP request timed out"):
            runtime.submit_http({}, timeout=0.05)
        assert entered.wait(1), "the registered HTTP handler must actually receive the call"
        release.set()
        assert closed.wait(1), "a timed-out request must still close its handler result"

        def invalid_http_start(payload, events, cancel):
            # Corrupt the current HTTP worker protocol, rather than the unrelated call queue.
            try:
                events.put(("invalid",))
            finally:
                with runtime._stream_lock:
                    runtime._active_streams.pop(cancel, None)

        monkeypatch.setattr(runtime, "_run_http", invalid_http_start)
        with pytest.raises(RuntimeError, match="invalid environment runtime HTTP response"):
            runtime.submit_http({}, timeout=1)
    finally:
        release.set()
        _restore_http_handler()
        runtime.stop()


def test_runtime_http_worker_propagates_handler_error(tmp_path):
    runtime = _runtime(tmp_path, "runtime-http-error")

    def fail_http(context, payload):
        raise ValueError("http handler failed")

    register_runtime_operation("http.request", fail_http)
    runtime.start()
    try:
        with pytest.raises(ValueError, match="http handler failed"):
            dispatch_environment_http(runtime.runtime_id, {}, timeout=1)
    finally:
        _restore_http_handler()
        runtime.stop()


def test_runtime_http_client_disconnect_cancels_body(tmp_path):
    runtime = _runtime(tmp_path, "runtime-http-disconnect")
    body_entered = threading.Event()
    release_body = threading.Event()

    def http_handler(context, payload):
        def body():
            body_entered.set()
            release_body.wait(1)
            yield b"late"

        return {
            "status_code": 200,
            "headers": [],
            "body": body(),
            "close": lambda: None,
        }

    register_runtime_operation("http.request", http_handler)
    register_runtime_operation("test.sync", lambda context, payload: True)
    runtime.start()
    try:
        stream = dispatch_environment_http(runtime.runtime_id, {"base_path": "/runtime"}, timeout=1)
        assert body_entered.wait(1)
        stream.close()
        release_body.set()
        assert dispatch_environment_operation(runtime.runtime_id, "test.sync") is True
    finally:
        release_body.set()
        _restore_http_handler()
        runtime.stop()


def test_runtime_stop_breaks_stream_backpressure_without_hanging(tmp_path):
    runtime = _runtime(tmp_path, "runtime-http-backpressure")

    def http_handler(context, payload):
        return {
            "status_code": 200,
            "headers": [],
            "body": (b"x" for _ in range(1000)),
            "close": lambda: None,
        }

    register_runtime_operation("http.request", http_handler)
    runtime.start()
    try:
        stream = dispatch_environment_http(runtime.runtime_id, {}, timeout=1)
        for _ in range(100):
            if stream._events.qsize() == 8:
                break
            time.sleep(0.001)
        assert stream._events.qsize() == 8

        runtime.stop()
        assert list(stream) == []
    finally:
        _restore_http_handler()
        if runtime.thread_id and runtime._thread.is_alive():
            runtime.stop()


def test_runtime_stop_cancels_request_before_stream_start(tmp_path):
    runtime = _runtime(tmp_path, "runtime-http-start-cancel")
    handler_entered = threading.Event()
    release_handler = threading.Event()
    caller_errors = []

    def http_handler(context, payload):
        handler_entered.set()
        release_handler.wait(1)
        return {
            "status_code": 200,
            "headers": [],
            "body": (),
            "close": lambda: None,
        }

    def call_http():
        try:
            dispatch_environment_http(runtime.runtime_id, {}, timeout=1)
        except Exception as exc:
            caller_errors.append(exc)

    register_runtime_operation("http.request", http_handler)
    runtime.start()
    caller = threading.Thread(target=call_http)
    caller.start()
    assert handler_entered.wait(1)

    stopper = threading.Thread(target=runtime.stop)
    stopper.start()
    for _ in range(100):
        with runtime._stream_lock:
            cancelled = any(cancel.is_set() for cancel in runtime._active_streams)
        if cancelled:
            break
        time.sleep(0.001)
    assert cancelled

    release_handler.set()
    caller.join(timeout=1)
    stopper.join(timeout=1)

    try:
        assert not caller.is_alive()
        assert not stopper.is_alive()
        assert caller_errors
        assert isinstance(caller_errors[0], RuntimeError)
        assert "invalid environment runtime HTTP response" in str(caller_errors[0])
    finally:
        _restore_http_handler()


def test_runtime_stop_reports_hung_worker(tmp_path):
    runtime = _runtime(tmp_path, "runtime-hung")

    class HungThread:
        ident = 7

        def is_alive(self):
            return True

        def join(self, timeout):
            return None

    runtime._thread = HungThread()

    with pytest.raises(RuntimeError, match="did not stop"):
        runtime.stop()


def test_dispatch_helpers_reject_unknown_runtime():
    with pytest.raises(RuntimeError, match="not running"):
        dispatch_environment_operation("missing-runtime", "test.echo")
    with pytest.raises(RuntimeError, match="not running"):
        dispatch_environment_http("missing-runtime", {})


def test_runtime_stop_tolerates_full_stream_queue_during_cancel(tmp_path):
    runtime = _runtime(tmp_path, "runtime-stop-full-queue")
    cancel = threading.Event()

    class RefillingQueue:
        def get_nowait(self):
            raise queue.Empty

        def put_nowait(self, _event):
            raise queue.Full

    register_runtime_operation("test.idle", lambda context, payload: None)
    runtime.start()
    with runtime._stream_lock:
        runtime._active_streams[cancel] = RefillingQueue()

    runtime.stop()

    assert cancel.is_set()


def test_runtime_worker_encodes_text_and_stops_when_chunk_delivery_is_cancelled(
    tmp_path, monkeypatch
):
    runtime = _runtime(tmp_path, "runtime-text-cancel")
    closed = []
    original_stream_put = environment_manager._stream_put

    def controlled_stream_put(events, event, cancel):
        if event[0] == "chunk":
            assert event[1] == b"text"
            return False
        return original_stream_put(events, event, cancel)

    def http_handler(context, payload):
        return {
            "status_code": 200,
            "headers": [],
            "body": ["text"],
            "close": lambda: closed.append(True),
        }

    monkeypatch.setattr(environment_manager, "_stream_put", controlled_stream_put)
    register_runtime_operation("http.request", http_handler)
    runtime.start()
    try:
        stream = dispatch_environment_http(runtime.runtime_id, {}, timeout=1)
        assert list(stream) == []
        assert closed == [True]
    finally:
        _restore_http_handler()
        runtime.stop()


def test_timed_out_stop_blocks_new_calls_preserves_data_and_allows_resume(tmp_path, monkeypatch):
    monkeypatch.setenv("LOGOS_ENV_RUNTIME_ROOT", str(tmp_path / "runtimes"))
    runtime = _runtime(tmp_path, "runtime-stop-admission")
    entered = threading.Event()
    release = threading.Event()
    caller_errors = []

    def http_handler(context, payload):
        entered.set()
        release.wait()
        return {"status_code": 200, "headers": [], "body": (), "close": lambda: None}

    def call_http():
        try:
            runtime.submit_http({}, timeout=2)
        except RuntimeError as exc:
            caller_errors.append(exc)

    register_runtime_operation("http.request", http_handler)
    register_runtime_operation("test.resume", lambda context, payload: context.runtime_id)
    runtime.start()
    runtime.data_dir.mkdir(parents=True, exist_ok=True)
    marker = runtime.data_dir / "session.txt"
    marker.write_text("retain session")
    caller = threading.Thread(target=call_http)
    caller.start()
    resumed = None
    try:
        assert entered.wait(2)
        http_thread = runtime._running_http_requests()[0]
        original_join = http_thread.join
        # Exercise the timeout path without making this regression test wait five seconds.
        monkeypatch.setattr(http_thread, "join", lambda timeout: None)
        with pytest.raises(RuntimeError, match="HTTP request is still running"):
            runtime.stop()
        monkeypatch.setattr(http_thread, "join", original_join)

        with pytest.raises(RuntimeError, match="runtime is stopping"):
            runtime.submit("test.resume")
        with pytest.raises(RuntimeError, match="runtime is stopping"):
            runtime.submit_http({})
        with pytest.raises(RuntimeError, match="must stop before removal"):
            EnvironmentRuntime(runtime.environment).remove()
        assert marker.read_text() == "retain session"
        assert _RUNTIME_WORKERS[runtime.runtime_id] is runtime

        release.set()
        original_join(timeout=2)
        caller.join(timeout=2)
        runtime.stop()
        resumed = _runtime(tmp_path, runtime.runtime_id)
        resumed.start()
        assert resumed.submit("test.resume") == runtime.runtime_id
        assert marker.read_text() == "retain session"
        assert caller_errors
    finally:
        release.set()
        caller.join(timeout=2)
        _restore_http_handler()
        if resumed is not None:
            resumed.stop()
        elif runtime._thread.is_alive():
            runtime.stop()


def test_delete_failed_environment_retries_live_worker_before_removing_record(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path, "runtime-failed-delete")
    item = {**runtime.environment, "status": "FAILED"}
    persisted = []
    record_reads = []

    def require(environment_id, owner_id):
        record_reads.append(environment_id)
        return dict(item)

    def hung_stop():
        raise RuntimeError("environment HTTP request is still running")

    monkeypatch.setattr(environment_manager, "_require", require)
    monkeypatch.setattr(
        environment_manager, "_persist_environment", lambda *args: persisted.append(args)
    )
    monkeypatch.setattr(runtime, "stop", hung_stop)
    with _RUNTIME_WORKERS_LOCK:
        _RUNTIME_WORKERS[runtime.runtime_id] = runtime
    try:
        with pytest.raises(RuntimeError, match="HTTP request is still running"):
            environment_manager.delete_environment(runtime.runtime_id)
        assert record_reads == [runtime.runtime_id, runtime.runtime_id]
        assert persisted == [
            (
                runtime.runtime_id,
                {"status": "FAILED", "error": "environment HTTP request is still running"},
            )
        ]
        assert _RUNTIME_WORKERS[runtime.runtime_id] is runtime
    finally:
        with _RUNTIME_WORKERS_LOCK:
            _RUNTIME_WORKERS.pop(runtime.runtime_id, None)

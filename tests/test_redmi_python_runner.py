"""Offline regression tests; no GitHub credentials or paid API calls."""
import importlib.util
import base64
import json
import os
from pathlib import Path
import threading
import time
from types import SimpleNamespace
import uuid

import pytest

spec = importlib.util.spec_from_file_location(
    "redmi_python_runner", Path(__file__).parents[1] / "scripts/redmi_python_runner.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def message(script="printf native-ok", shell="bash"):
    return {
        "jobId": str(uuid.uuid4()), "plan": {"planId": str(uuid.uuid4())},
        "contextData": {"github": {"repository": "owner/repo", "event_name": "push"}},
        "resources": {"endpoints": [{"name": "SystemVssConnection",
            "url": "https://test.actions.githubusercontent.com/",
            "authorization": {"parameters": {"AccessToken": "test-secret-token"}}}]},
        "steps": [{"id": str(uuid.uuid4()), "name": "native", "reference": {"type": 3},
            "inputs": {"type": 2, "map": [
                {"Key": "script", "Value": script}, {"Key": "shell", "Value": shell}]}}],
    }


def execute_job(tmp_path, payload):
    listener = SimpleNamespace(state=tmp_path, config={"repo": "owner/repo", "name": "test-runner"},
                               stop=threading.Event())
    job = runner.Job(listener, payload, {})
    calls = []
    job.request = lambda path, data: calls.append((path, data))
    job.run()
    return calls[-1], json.loads((tmp_path / "last-result.json").read_text())


@pytest.mark.parametrize("script,wire,exit_code", [
    ("printf native-ok", "succeeded", 0), ("exit 17", "failed", 17)])
def test_real_exit_status_and_wire_enum(tmp_path, script, wire, exit_code):
    (path, result), receipt = execute_job(tmp_path, message(script))
    assert path == "completejob"
    # Integer enum values previously produced a false success on the server.
    assert result["conclusion"] == wire
    assert result["stepResults"][0]["conclusion"] == wire
    assert result["stepResults"][0]["status"] == "completed"
    assert receipt["steps"][0]["exit_code"] == exit_code


def test_uppercase_template_map_and_context_data():
    assert runner.decode(message()["steps"][0]["inputs"]) == {"script": "printf native-ok", "shell": "bash"}
    assert runner.decode({"t": 2, "d": [{"k": "repository", "v": {"t": 0, "s": "owner/repo"}}]}) == {"repository": "owner/repo"}


@pytest.mark.parametrize("change", ["fork", "repository", "uses", "expression", "empty", "container"])
def test_unsupported_job_fails_without_running(tmp_path, change):
    marker = tmp_path / "must-not-exist"
    payload = message(f"touch {marker}")
    github = payload["contextData"]["github"]
    if change == "fork":
        github.update(event_name="pull_request", event={"pull_request": {"head": {"repo": {"full_name": "stranger/repo", "fork": True}}}})
    elif change == "repository":
        github["repository"] = "other/repo"
    elif change == "uses":
        payload["steps"][0]["reference"] = {"type": 1, "name": "actions/checkout"}
    elif change == "expression":
        payload["steps"][0]["inputs"]["map"][0]["Value"] = {"type": 3, "expr": "unresolved"}
    elif change == "empty":
        payload["steps"] = []
    else:
        payload["jobContainer"] = {"image": "ubuntu"}
    (_, result), receipt = execute_job(tmp_path, payload)
    assert result["conclusion"] == "failed"
    assert receipt["conclusion"] == 2
    assert not marker.exists()


@pytest.mark.parametrize("script", ["sleep 10", "exec >/dev/null 2>&1; sleep 10"])
def test_timeout_even_after_stdout_closes(tmp_path, script):
    code, output = runner.execute_script(script, "bash", tmp_path, dict(os.environ), threading.Event(), timeout=0.15)
    assert code == 124
    assert "timeout" in output


def test_cancellation(tmp_path):
    cancel = threading.Event()
    cancel.set()
    code, _ = runner.execute_script("sleep 10", "bash", tmp_path, dict(os.environ), cancel)
    assert code == 130


def test_secret_redaction(tmp_path):
    (_, result), receipt = execute_job(tmp_path, message("printf test-secret-token"))
    assert "test-secret-token" not in json.dumps(result)
    assert receipt["steps"][0]["output"] == "***"


def test_failed_completion_does_not_write_success_receipt(tmp_path):
    listener = SimpleNamespace(state=tmp_path, config={"repo": "owner/repo", "name": "test"}, stop=threading.Event())
    job = runner.Job(listener, message(), {})
    def unavailable(*args):
        raise runner.APIError(503, "unavailable")
    job.request = unavailable
    job.run()
    assert listener.stop.is_set()
    assert not (tmp_path / "last-result.json").exists()
    assert (tmp_path / "unreported-result.json").exists()


def test_redirect_cannot_forward_credentials():
    with pytest.raises(runner.APIError, match="Redirect refused"):
        runner.NoRedirect().redirect_request(None, None, 302, "", {}, "https://example.org")


def test_ephemeral_token_only_in_explicit_env_and_masked(tmp_path):
    payload = message('printf "%s\\n" "$GH_TOKEN"; printf "%s" "x-access-token:$GH_TOKEN" | base64')
    payload["contextData"]["github"]["token"] = "ephemeral-test-secret"
    payload["steps"][0]["environment"] = {"map": [
        {"Key": "GH_TOKEN", "Value": {"type": 3, "expr": "github.token"}}]}
    (_, result), receipt = execute_job(tmp_path, payload)
    assert result["conclusion"] == "succeeded"
    assert "ephemeral-test-secret" not in json.dumps(receipt)
    assert base64.b64encode(b"x-access-token:ephemeral-test-secret").decode() not in json.dumps(receipt)
    assert receipt["steps"][0]["output"].splitlines() == ["***", "***"]
    payload = message('test -z "${GH_TOKEN-}"')
    payload["contextData"]["github"]["token"] = "ephemeral-test-secret"
    assert execute_job(tmp_path, payload)[0][1]["conclusion"] == "succeeded"


def test_other_env_expressions_rejected_before_execution(tmp_path):
    marker = tmp_path / "must-not-run"
    payload = message(f"touch {marker}")
    payload["steps"][0]["environment"] = {"map": [
        {"Key": "X", "Value": {"type": 3, "expr": "secrets.UNKNOWN"}}]}
    assert execute_job(tmp_path, payload)[0][1]["conclusion"] == "failed"
    assert not marker.exists()


@pytest.mark.parametrize("reject_metadata", [False, True])
def test_standard_logs_upload_masking_and_failure(tmp_path, monkeypatch, reject_metadata):
    payload = message("printf test-secret-token")
    payload["variables"] = {"system.github.results_endpoint": {"value": "https://results.actions.githubusercontent.com/"}}
    uploads, metadata = [], []
    def request(api, url, token, method="GET", data=None, **kwargs):
        if url.endswith("SignedBlobURL"):
            return {"logs_url": "https://logs.blob.core.windows.net/test?sig=secret",
                    "blob_storage_type": "BLOB_STORAGE_TYPE_AZURE", "soft_size_limit": 10000}
        metadata.append((url, data))
        return {"ok": not reject_metadata}
    monkeypatch.setattr(runner.API, "request", request)
    monkeypatch.setattr(runner.API, "upload_blob", lambda api, url, content, kind: uploads.append(content))
    (_, result), receipt = execute_job(tmp_path, payload)
    assert result["conclusion"] == ("failed" if reject_metadata else "succeeded")
    assert all(b"test-secret-token" not in content and b"***" in content for content in uploads)
    assert len(uploads) == (1 if reject_metadata else 2)
    assert metadata[0][1]["step_backend_id"] == payload["steps"][0]["id"]
    assert metadata[0][1]["line_count"] == 1
    assert list((tmp_path / "work" / payload["jobId"]).glob("step-*-receipt.json"))


def test_signed_storage_upload_has_no_bearer_header(monkeypatch):
    requests = []
    class Response:
        status = 201
        def __enter__(self): return self
        def __exit__(self, *args): pass
    class Opener:
        def open(self, request, timeout):
            requests.append(request)
            return Response()
    monkeypatch.setattr(runner.urllib.request, "build_opener", lambda *args: Opener())
    runner.API().upload_blob("https://logs.blob.core.windows.net/test?sig=secret", b"test", "BLOB_STORAGE_TYPE_AZURE")
    assert "authorization" not in {k.lower(): v for k, v in requests[0].header_items()}
    assert requests[0].get_header("X-ms-blob-type") == "BlockBlob"
    for url in ("http://logs.blob.core.windows.net/x", "https://example.org/x", "https://127.0.0.1/x"):
        with pytest.raises(ValueError):
            runner.API().upload_blob(url, b"test", "BLOB_STORAGE_TYPE_AZURE")


@pytest.mark.parametrize("heartbeat", [False, True])
def test_next_job_arriving_during_poll_after_worker_finishes(tmp_path, monkeypatch, heartbeat):
    first_started = threading.Event()
    release_first = threading.Event()
    acquired = []
    workers = []
    real_thread = threading.Thread
    listener = SimpleNamespace(state=tmp_path, stop=threading.Event(), broker="https://test.actions.githubusercontent.com/")
    listener.connect = lambda: None
    listener.disconnect = lambda: None
    listener.authorize = lambda: "test-token"
    listener.parameters = lambda status: "status=" + status
    class Worker:
        def __init__(self, parent, job, reference):
            self.id = job["jobId"]
            self.cancel = threading.Event()
            acquired.append(self.id)
        def run(self):
            if self.id == "1":
                first_started.set()
                assert release_first.wait(2)
            else:
                listener.stop.set()
    class FakeAPI:
        polls = 0
        def request(self, url, token, *args, **kwargs):
            if url.endswith("acquirejob"):
                return {"jobId": "1" if self.polls == 1 else "2", "steps": []}
            self.polls += 1
            if self.polls == 2:
                assert first_started.wait(2)
                release_first.set()
                workers[0].join(2)
                assert not workers[0].is_alive()
                if heartbeat:
                    time.sleep(0.5)  # No message; only the absolute deadline can wake us.
            if self.polls > (3 if heartbeat else 2):
                listener.stop.set()
                return None
            return {"messageType": "RunnerJobRequest", "body": {"runner_request_id": str(self.polls),
                    "run_service_url": "https://test.actions.githubusercontent.com/"}}
    def make_thread(*args, **kwargs):
        thread = real_thread(*args, **kwargs)
        workers.append(thread)
        return thread
    listener.api = FakeAPI()
    monkeypatch.setattr(runner, "Job", Worker)
    monkeypatch.setattr(runner.threading, "Thread", make_thread)
    real_signal, real_timer = runner.signal.signal, runner.signal.setitimer
    original_alarm = runner.signal.getsignal(runner.signal.SIGALRM)
    monkeypatch.setattr(runner.signal, "signal", lambda sig, handler:
                        real_signal(sig, handler) if sig == runner.signal.SIGALRM else None)
    monkeypatch.setattr(runner.signal, "setitimer", lambda kind, seconds:
                        real_timer(kind, 0.1 if seconds else 0))
    try:
        runner.run_listener(listener)
    finally:
        real_timer(runner.signal.ITIMER_REAL, 0)
        real_signal(runner.signal.SIGALRM, original_alarm)
    assert acquired == ["1", "2"]

"""Offline regression tests; no GitHub credentials or paid API calls."""
import importlib.util
import json
import os
from pathlib import Path
import threading
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


def test_next_job_arriving_during_poll_after_worker_finishes(tmp_path, monkeypatch):
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
                return {"jobId": str(self.polls), "steps": []}
            self.polls += 1
            if self.polls == 2:
                assert first_started.wait(2)
                release_first.set()
                workers[0].join(2)
                assert not workers[0].is_alive()
            if self.polls > 2:
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
    monkeypatch.setattr(runner.signal, "signal", lambda *args: None)
    runner.run_listener(listener)
    assert acquired == ["1", "2"]

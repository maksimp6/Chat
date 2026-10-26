"""Queue-independent phone updates, using isolated state and no network or credentials."""

import fcntl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts/redmi_app_autoupdate.py"
BOOT = SCRIPT.with_name("redmi_app_autoupdate_boot.sh")
spec = importlib.util.spec_from_file_location("redmi_app_autoupdate", SCRIPT)
updater = importlib.util.module_from_spec(spec)
spec.loader.exec_module(updater)
OLD, NEW, NEWER = "a" * 40, "b" * 40, "c" * 40


@pytest.fixture
def installation(tmp_path, monkeypatch):
    app, state = tmp_path / "app", tmp_path / "state"
    (app / "scripts").mkdir(parents=True)
    (app / ".venv/bin").mkdir(parents=True)
    state.mkdir()
    (app / "app.py").write_text("# synthetic app\n")
    (app / "scripts/redmi_app_server.sh").write_text("# synthetic controller\n")
    (state / "backend.pid").write_text("111")
    current = {"revision": OLD, "remote": OLD, "ready": True, "owned": True, "health": True}
    calls = []
    monkeypatch.setattr(updater, "app_revision", lambda *_: current["revision"])
    monkeypatch.setattr(updater, "remote_revision", lambda *_: current["remote"])
    monkeypatch.setattr(updater, "ready", lambda *_: current["ready"])
    monkeypatch.setattr(updater, "process_owned", lambda *_: current["owned"])
    monkeypatch.setattr(updater, "http_healthy", lambda *_: current["health"])

    def controller(app, env, action, timeout):
        calls.append((action, env, timeout))
        assert env["LOGOS_DEPLOY_TRIGGER"] == "local_autoupdate"
        assert "GITHUB_TOKEN" not in env
        current.update(revision=current["remote"], ready=True, owned=True, health=True)
        (state / "backend.pid").write_text("222")
        return 0

    monkeypatch.setattr(updater, "run_controller", controller)
    return app, state, current, calls


def test_unchanged_healthy_revision_does_not_restart(installation):
    app, state, current, calls = installation
    result = updater.check_once(app, state)
    assert result["result"] == "unchanged"
    assert result["pid_before"] == result["pid_after"] == 111
    assert result["latest_revision_verified"] is True
    assert calls == []
    assert not (state / "autoupdate-last-deploy.json").exists()


def test_new_revision_deploys_and_persists_actual_receipt(installation, monkeypatch):
    app, state, current, calls = installation
    monkeypatch.setenv("GITHUB_TOKEN", "must-not-be-forwarded")
    current["remote"] = NEW
    result = updater.check_once(app, state)
    assert result["result"] == "deployed"
    assert result["observed_revision"] == result["app_revision"] == NEW
    assert result["pid_before"] == 111 and result["pid_after"] == 222
    assert len(calls) == 1 and calls[0][0] == "deploy" and calls[0][2] == 600
    assert json.loads((state / "autoupdate-last-deploy.json").read_text()) == result
    assert json.loads((state / "autoupdate-status.json").read_text()) == result


@pytest.mark.parametrize("broken", ["ready", "owned", "health"])
def test_current_but_unready_installation_is_recovered(installation, broken):
    app, state, current, calls = installation
    current[broken] = False
    current["ready"] = False
    result = updater.check_once(app, state)
    assert result["result"] == "recovered"
    assert result["app_revision"] == OLD
    assert result["healthy_after"] is True
    assert calls[0][0] == "deploy"


def test_deploy_failure_is_error_and_next_check_retries(installation, monkeypatch):
    app, state, current, calls = installation
    current["remote"] = NEW
    real_controller = updater.run_controller
    monkeypatch.setattr(updater, "run_controller", lambda *_: 17)
    failed = updater.check_once(app, state)
    assert failed["result"] == "error" and failed["error"] == "deploy_failed"
    assert failed["latest_revision_verified"] is False
    assert failed["deploy_exit_code"] == 17
    monkeypatch.setattr(updater, "run_controller", real_controller)
    assert updater.check_once(app, state)["result"] == "deployed"


def test_exit_zero_is_not_proof_of_deployment(installation, monkeypatch):
    app, state, current, calls = installation
    current["remote"] = NEW
    monkeypatch.setattr(updater, "run_controller", lambda *_: 0)
    result = updater.check_once(app, state)
    assert result["result"] == "error"
    assert result["error"] == "deploy_verification_failed"
    assert result["latest_revision_verified"] is False


def test_remote_advancing_during_deploy_verifies_newest_head(installation, monkeypatch):
    app, state, current, calls = installation
    current["remote"] = NEW
    controller = updater.run_controller

    def advanced(*args):
        current["remote"] = NEWER
        return controller(*args)

    monkeypatch.setattr(updater, "run_controller", advanced)
    result = updater.check_once(app, state)
    assert result["observed_revision"] == NEW
    assert result["app_revision"] == result["verified_remote_revision"] == NEWER
    assert result["result"] == "deployed"


def test_disabled_marker_prevents_every_external_operation(installation, monkeypatch):
    app, state, current, calls = installation
    (state / "autoupdate.disabled").touch()
    monkeypatch.setattr(updater, "remote_revision", lambda *_: pytest.fail("disabled worker contacted GitHub"))
    result = updater.check_once(app, state)
    assert result["result"] == "disabled" and calls == []


def test_worker_lock_prevents_concurrent_updates(installation):
    app, state, current, calls = installation
    with updater.iteration_lock(state) as acquired:
        assert acquired
        result = updater.check_once(app, state)
    assert result["result"] == "busy" and calls == []
    assert not (state / "autoupdate-status.json").exists()


def test_worker_refreshes_state_copy_only_after_success(installation, monkeypatch):
    app, state, current, calls = installation
    source = app / "scripts/redmi_app_autoupdate.py"
    source.write_text("print('new worker')\n")
    (state / "app-autoupdate.py").write_text("old worker")
    current["remote"] = NEW
    controller = updater.run_controller
    monkeypatch.setattr(updater, "run_controller", lambda *_: 2)
    assert updater.check_once(app, state)["result"] == "error"
    assert (state / "app-autoupdate.py").read_text() == "old worker"
    monkeypatch.setattr(updater, "run_controller", controller)
    assert updater.check_once(app, state)["result"] == "deployed"
    assert (state / "app-autoupdate.py").read_bytes() == source.read_bytes()
    assert (state / "app-autoupdate.py").stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize("owned,action", [(False, "start"), (True, "restart")])
def test_offline_github_does_not_prevent_local_recovery(installation, monkeypatch, owned, action):
    app, state, current, calls = installation
    current.update(ready=False, owned=owned, health=False)

    def unavailable(*_):
        raise updater.UpdateFailure("remote_unavailable")

    monkeypatch.setattr(updater, "remote_revision", unavailable)
    result = updater.check_once(app, state)
    assert result["result"] == "recovered_local"
    assert result["latest_revision_verified"] is False
    assert result["healthy_after"] is True
    assert result["error"] == "remote_unavailable"
    assert calls[0][0] == action


def test_offline_healthy_app_keeps_running_without_false_latest_claim(installation, monkeypatch):
    app, state, current, calls = installation
    monkeypatch.setattr(updater, "remote_revision", lambda *_: (_ for _ in ()).throw(updater.UpdateFailure("remote_unavailable")))
    result = updater.check_once(app, state)
    assert result["result"] == "error" and result["error"] == "remote_unavailable"
    assert result["latest_revision_verified"] is False
    assert calls == []


def test_ready_uses_actual_receipt_process_and_http(tmp_path, monkeypatch):
    app, state = tmp_path / "app", tmp_path / "state"
    app.mkdir(); state.mkdir()
    (state / "backend.pid").write_text("123")
    (state / "runner-start.json").write_text(json.dumps({"status": "ready", "app_revision": OLD, "target_revision": "", "pid": 123}))
    monkeypatch.setattr(updater, "process_owned", lambda actual_app, pid: actual_app == app and pid == 123)
    monkeypatch.setattr(updater, "http_healthy", lambda port: port == 8080)
    assert updater.ready(app, state, OLD, 8080)
    assert not updater.ready(app, state, NEW, 8080)
    (state / "backend.pid").write_text("124")
    assert not updater.ready(app, state, OLD, 8080)


@pytest.fixture
def health_server():
    servers = []

    def serve(status, body):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        servers.append(server)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server.server_address[1]

    yield serve
    for server in servers:
        server.shutdown()
        server.server_close()


# The real /healthz body from app.py must pass; only the status field is the contract.
@pytest.mark.parametrize("status,body,expected", [
    (200, b'{"status":"ok","scope":"process","chat_verified":false}', True),
    (200, b'{"status":"ok"}', True),
    (200, b'{"status":"failed"}', False),
    (503, b'{"status":"ok"}', False),
    (200, b'[]', False),
    (200, b'not JSON', False),
    (200, b'{"status":"ok","pad":"' + b"x" * 4096 + b'"}', False),
])
def test_http_healthy_checks_the_status_field_of_the_real_health_contract(health_server, status, body, expected):
    assert updater.http_healthy(health_server(status, body)) is expected


def test_process_identity_uses_cwd_command_and_non_zombie_state(tmp_path, monkeypatch):
    app, proc = tmp_path / "app", tmp_path / "proc"
    app.mkdir(); process = proc / "123"; process.mkdir(parents=True)
    (process / "cwd").symlink_to(app, target_is_directory=True)
    (process / "cmdline").write_bytes(str(app / ".venv/bin/python").encode() + b"\0app.py\0")
    (process / "stat").write_text("123 (synthetic python) S 0")
    monkeypatch.setattr(updater, "Path", lambda value: proc if value == "/proc" else Path(value))
    signals = []
    monkeypatch.setattr(updater.os, "kill", lambda pid, sig: signals.append((pid, sig)))
    assert updater.process_owned(app, 123)
    assert signals == [(123, 0)]
    (process / "cmdline").write_bytes(b"unrelated-process\0")
    assert not updater.process_owned(app, 123)
    assert signals == [(123, 0)]
    (process / "cmdline").write_bytes(str(app / ".venv/bin/python").encode() + b"\0app.py\0")
    (process / "stat").write_text("123 (synthetic python) Z 0")
    assert not updater.process_owned(app, 123)


def test_remote_read_is_public_bounded_and_does_not_use_credential_helpers(monkeypatch, tmp_path):
    seen = []

    def run(command, **kwargs):
        seen.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout=NEW + "\trefs/heads/develop\n")

    monkeypatch.setattr(updater.subprocess, "run", run)
    env = updater.environment(tmp_path / "app", tmp_path / "state", 8080)
    assert updater.remote_revision(env) == NEW
    command, options = seen[0]
    assert "credential.helper=" in command and "http.extraHeader=" in command
    assert updater.REMOTE in command and command[-1] == "refs/heads/develop"
    assert options["timeout"] == 20 and options["env"]["GIT_TERMINAL_PROMPT"] == "0"


def test_controller_timeout_terminates_only_its_own_session(monkeypatch, tmp_path):
    app = tmp_path / "app"; (app / "scripts").mkdir(parents=True)
    (app / "scripts/redmi_app_server.sh").touch()
    sent, started = [], []

    class Process:
        pid = 456789
        calls = 0

        def communicate(self, timeout):
            self.calls += 1
            if self.calls < 3:
                raise subprocess.TimeoutExpired("synthetic controller", timeout)
            return "", ""

    def popen(command, **kwargs):
        started.append((command, kwargs))
        return Process()

    monkeypatch.setattr(updater.subprocess, "Popen", popen)
    monkeypatch.setattr(updater.os, "killpg", lambda pid, sig: sent.append((pid, sig)))
    with pytest.raises(updater.UpdateFailure, match="deploy_timeout"):
        updater.run_controller(app, {}, "deploy", 600)
    assert started[0][1]["start_new_session"] is True
    assert sent == [(456789, signal.SIGTERM), (456789, signal.SIGKILL)]


def test_supervisor_lock_refuses_a_second_worker(tmp_path):
    app, state = tmp_path / "app", tmp_path / "state"
    (app / ".venv/bin").mkdir(parents=True); state.mkdir()
    (app / ".venv/bin/python").symlink_to(sys.executable)
    marker = state / "must-not-run"
    (state / "app-autoupdate.py").write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")
    env = dict(os.environ, LOGOS_APP_DIR=str(app), LOGOS_STATE_DIR=str(state), LOGOS_AUTOUPDATE_INTERVAL="1")
    lock_path = state / "autoupdate-supervisor.lock"
    with lock_path.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        lock.write("existing-supervisor\n"); lock.flush()
        result = subprocess.run(["bash", str(BOOT), "run"], env=env, capture_output=True, text=True, timeout=3)
    assert result.returncode == 0
    assert lock_path.read_text() == "existing-supervisor\n"
    assert not marker.exists()


def test_supervisor_retries_a_crashing_worker(tmp_path):
    app, state = tmp_path / "app", tmp_path / "state"
    (app / ".venv/bin").mkdir(parents=True); state.mkdir()
    (app / ".venv/bin/python").symlink_to(sys.executable)
    marker = state / "checks"
    (state / "app-autoupdate.py").write_text(
        "from pathlib import Path\n"
        f"with Path({str(marker)!r}).open('a') as stream: stream.write('check\\n')\n"
        "raise SystemExit(17)\n"
    )
    env = dict(os.environ, LOGOS_APP_DIR=str(app), LOGOS_STATE_DIR=str(state), LOGOS_AUTOUPDATE_INTERVAL="1")
    with pytest.raises(subprocess.TimeoutExpired):
        subprocess.run(["bash", str(BOOT), "run"], env=env, capture_output=True, text=True, timeout=2.4)
    assert marker.read_text().count("check") >= 2

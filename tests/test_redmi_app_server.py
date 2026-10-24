"""Real subprocess/HTTP tests with an isolated synthetic app; no phone app or models."""
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts/redmi_app_server.sh"


@pytest.fixture
def installation(tmp_path):
    app, state = tmp_path / "app", tmp_path / "state"
    (app / ".venv/bin").mkdir(parents=True)
    (app / "static").mkdir()
    state.mkdir()
    (app / ".venv/bin/python").symlink_to(sys.executable)
    for name in ("chat.js", "style.css"):
        (app / "static" / name).write_text("fixture " + name)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    (app / "app.py").write_text('''import http.server, json, os
from pathlib import Path
state = Path(os.environ["LOGOS_STATE_DIR"])
(state / "child-env.json").write_text(json.dumps({k: k in os.environ for k in ("GH_TOKEN", "GITHUB_TOKEN", "ACTIONS_RUNTIME_TOKEN", "RUNNER_TRACKING_ID")}))
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/healthz": body = b'{"status":"ok"}'
        elif self.path == "/": body = b"<html><title>Logos</title></html>"
        elif self.path.startswith("/static/"): body = Path(self.path.lstrip("/")).read_bytes()
        else: self.send_error(404); return
        self.send_response(200); self.end_headers(); self.wfile.write(body)
    def log_message(self, *args): pass
http.server.HTTPServer(("127.0.0.1", int(os.environ["LOGOS_PORT"])), Handler).serve_forever()
''')
    (state / "start-logos.py").write_text('''import os, pathlib, subprocess
app = pathlib.Path(os.environ["LOGOS_APP_DIR"])
state = pathlib.Path(os.environ["LOGOS_STATE_DIR"])
with (state / "launches").open("a") as count: count.write("start\\n")
with (state / "backend.log").open("ab") as log:
    p = subprocess.Popen([str(app / ".venv/bin/python"), "app.py"], cwd=app, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
(state / "backend.pid").write_text(str(p.pid))
''')
    (state / "logos.memory").write_bytes(b"existing-persistent-data")
    env = dict(os.environ, LOGOS_APP_DIR=str(app), LOGOS_STATE_DIR=str(state), LOGOS_PORT=str(port),
               LOGOS_START_TIMEOUT="3", GH_TOKEN="must-not-reach-app", GITHUB_TOKEN="must-not-reach-app",
               ACTIONS_RUNTIME_TOKEN="must-not-reach-app", RUNNER_TRACKING_ID="must-not-reach-app")
    try:
        yield app, state, env
    finally:
        # Stop only the process created by this isolated fixture.
        run("stop", env)


def run(action, env):
    return subprocess.run(["bash", str(SCRIPT), action], env=env, capture_output=True, text=True, timeout=25)


def test_start_reuse_restart_and_no_job_credentials(installation):
    app, state, env = installation
    first = run("start", env)
    assert first.returncode == 0, first.stderr
    original_pid = json.loads(first.stdout)["pid"]
    assert json.loads(run("start", env).stdout)["pid"] == original_pid
    assert (state / "launches").read_text().count("start") == 1
    restarted = run("restart", env)
    assert restarted.returncode == 0, restarted.stderr
    assert json.loads(restarted.stdout)["pid"] != original_pid
    assert (state / "logos.memory").read_bytes() == b"existing-persistent-data"
    assert not any(json.loads((state / "child-env.json").read_text()).values())
    assert json.loads(run("status", env).stdout)["status"] == "ready"


def test_simultaneous_start_does_not_duplicate(installation):
    app, state, env = installation
    procs = [subprocess.Popen(["bash", str(SCRIPT), "start"], env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True) for _ in range(2)]
    results = [p.communicate(timeout=25) for p in procs]
    assert all(p.returncode == 0 for p in procs), results
    assert len({json.loads(out)["pid"] for out, _ in results}) == 1
    assert (state / "launches").read_text().count("start") == 1


def test_foreign_pid_never_killed(installation):
    app, state, env = installation
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        (state / "backend.pid").write_text(str(other.pid))
        result = run("restart", env)
        assert result.returncode != 0 and other.poll() is None
        assert not (state / "launches").exists()
    finally:
        other.terminate()
        other.wait(timeout=5)
        (state / "backend.pid").unlink()


def test_occupied_port_never_claimed_as_own_server(installation):
    app, state, env = installation
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", int(env["LOGOS_PORT"])))
        listener.listen()
        result = run("start", env)
    assert result.returncode != 0
    assert "untracked listener" in result.stderr
    assert not (state / "launches").exists()


def test_failed_application_cannot_report_ready(installation):
    app, state, env = installation
    (app / "app.py").write_text("raise SystemExit(17)\n")
    result = run("start", env)
    assert result.returncode != 0
    assert not (state / "runner-start.json").exists()


def test_wrong_static_content_fails_readiness(installation):
    app, state, env = installation
    source = (app / "app.py").read_text()
    (app / "app.py").write_text(source.replace('body = Path(self.path.lstrip("/")).read_bytes()', 'body = b"wrong app assets"'))
    result = run("start", env)
    assert result.returncode != 0
    assert not (state / "runner-start.json").exists()

"""Real subprocess/HTTP tests with an isolated synthetic app; no phone app or models."""
import json
import hashlib
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import urllib.request

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
assert Path(os.environ["LOGOS_MEMORY_PATH"]).read_bytes() == b"existing-persistent-data"
assert os.environ["LOGOS_PROVIDER_CREDENTIAL_KEY"] == (state / "provider-encryption.key").read_text().strip()
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/healthz": body = b'{"status":"ok"}'
        elif self.path == "/": body = b"<html><title>Logos</title></html>"
        elif self.path.startswith("/static/"): body = Path(self.path.lstrip("/")).read_bytes()
        else: self.send_error(404); return
        self.send_response(200); self.end_headers(); self.wfile.write(body)
    def log_message(self, *args): pass
http.server.HTTPServer((os.environ.get("HOST", "127.0.0.1"), int(os.environ["LOGOS_PORT"])), Handler).serve_forever()
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
    (state / "provider-encryption.key").write_text("fixture-encryption-key")
    env = dict(os.environ, LOGOS_APP_DIR=str(app), LOGOS_STATE_DIR=str(state), LOGOS_PORT=str(port),
               LOGOS_START_TIMEOUT="3", LOGOS_REQUIRE_WIFI="0", GH_TOKEN="must-not-reach-app", GITHUB_TOKEN="must-not-reach-app",
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
    assert json.loads(first.stdout)["trigger"] == "manual"
    auto_env = dict(env, LOGOS_DEPLOY_TRIGGER="local_autoupdate")
    assert json.loads(run("status", auto_env).stdout)["trigger"] == "local_autoupdate"



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


def test_launcher_uses_network_config_and_preserves_storage(installation):
    app, state, env = installation
    (state / "native-deps/site-packages/cryptography").mkdir(parents=True)
    (state / "server-network.json").write_text('{"bind_host":"0.0.0.0"}')
    (state / "start-logos.py").write_text((SCRIPT.parent / "redmi_app_launcher.py").read_text())
    result = run("start", env)
    assert result.returncode == 0, result.stderr
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open("http://127.0.0.2:" + env["LOGOS_PORT"] + "/healthz", timeout=3) as response:
        assert response.status == 200 and json.load(response) == {"status": "ok"}
    assert (state / "logos.memory").read_bytes() == b"existing-persistent-data"
    assert (state / "provider-encryption.key").read_text() == "fixture-encryption-key"


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


@pytest.fixture
def deployment(installation, tmp_path):
    """Real local Git transport for the fixed public URL; no provider/network calls."""
    app, state, env = installation
    env.update(GITHUB_ACTIONS="true", GITHUB_REPOSITORY="maksimp6/Chat", GITHUB_REF="refs/heads/develop",
               GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null", GIT_TERMINAL_PROMPT="0")
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", str(origin)], check=True)
    subprocess.run(["git", "init", "-q", str(app)], check=True)
    git(app, "config", "user.name", "Test")
    git(app, "config", "user.email", "test@example.invalid")
    git(app, "add", "app.py", "static/chat.js", "static/style.css")
    git(app, "commit", "-qm", "working application")
    git(app, "branch", "-M", "develop")
    git(app, "remote", "add", "origin", "https://github.com/maksimp6/Chat.git")
    git(app, "config", "url." + origin.as_uri() + ".insteadOf", "https://github.com/maksimp6/Chat.git")
    git(app, "push", "-q", "origin", "develop")
    git(origin, "symbolic-ref", "HEAD", "refs/heads/develop")
    author = tmp_path / "author"
    subprocess.run(["git", "clone", "-q", str(origin), str(author)], check=True)
    git(author, "config", "user.name", "Test")
    git(author, "config", "user.email", "test@example.invalid")
    return app, state, env, origin, author


def publish(author, *, broken=False):
    source = (author / "app.py").read_text()
    (author / "app.py").write_text("raise SystemExit(17)\n" if broken else source.replace(
        '<title>Logos</title>', '<title>Logos</title><p>deployed-v2</p>'
    ))
    (author / "static/chat.js").write_text("updated application asset")
    git(author, "add", "app.py", "static/chat.js")
    git(author, "commit", "-qm", "publish application update")
    git(author, "push", "-q", "origin", "develop")
    return git(author, "rev-parse", "HEAD")


def page(env):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open("http://127.0.0.1:" + env["LOGOS_PORT"] + "/", timeout=3) as response:
        return response.read()


def test_deploy_updates_real_git_code_and_running_application(deployment):
    app, state, env, _, author = deployment
    previous = git(app, "rev-parse", "HEAD")
    initial = run("start", env)
    assert initial.returncode == 0, initial.stderr
    old_pid = json.loads(initial.stdout)["pid"]
    target = publish(author)
    result = run("deploy", env)
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["status"] == "ready" and receipt["outcome"] == "updated"
    assert receipt["app_revision"] == receipt["target_revision"] == target
    assert receipt["pid"] != old_pid
    assert b"deployed-v2" in page(env)
    assert git(app, "rev-parse", "HEAD") == target
    assert git(app, "rev-parse", "refs/logos/deployments/" + previous) == previous
    assert (app / ".venv/bin/python").is_symlink()
    assert (state / "logos.memory").read_bytes() == b"existing-persistent-data"
    assert (state / "provider-encryption.key").read_text() == "fixture-encryption-key"
    assert [p.read_bytes() for p in (state / "deploy-backups").glob("*.memory")] == [b"existing-persistent-data"]
    assert not any(json.loads((state / "child-env.json").read_text()).values())
    assert "fixture-encryption-key" not in result.stdout + result.stderr
    second = run("deploy", env)
    assert second.returncode == 0, second.stderr
    assert json.loads(second.stdout)["pid"] == receipt["pid"]
    assert json.loads(second.stdout)["outcome"] == "already_current"
    assert (state / "launches").read_text().count("start") == 2


def test_deploy_dirty_worktree_keeps_running_code(deployment):
    app, state, env, _, author = deployment
    initial = json.loads(run("start", env).stdout)
    publish(author)
    (app / "app.py").write_text((app / "app.py").read_text() + "# local work\n")
    result = run("deploy", env)
    assert result.returncode != 0 and "must be committed" in result.stderr
    assert json.loads(run("status", env).stdout)["pid"] == initial["pid"]
    assert b"deployed-v2" not in page(env)
    assert (state / "launches").read_text().count("start") == 1


def test_deploy_fetch_failure_keeps_running_application(deployment):
    app, state, env, origin, _ = deployment
    initial = json.loads(run("start", env).stdout)
    origin.rename(origin.with_name("unavailable.git"))
    result = run("deploy", env)
    assert result.returncode != 0 and "does not appear to be a git repository" in result.stderr
    assert json.loads(run("status", env).stdout)["pid"] == initial["pid"]
    assert b"<title>Logos</title>" in page(env)
    assert (state / "launches").read_text().count("start") == 1


def test_failed_deploy_restores_previous_code_and_reports_failure(deployment):
    app, state, env, _, author = deployment
    initial = json.loads(run("start", env).stdout)
    previous = git(app, "rev-parse", "HEAD")
    target = publish(author, broken=True)
    result = run("deploy", env)
    assert result.returncode != 0
    receipt = json.loads(result.stdout)
    assert receipt["status"] == "rolled_back" and receipt["target_revision"] == target
    assert receipt["app_revision"] == previous
    assert receipt["pid"] != initial["pid"]
    assert git(app, "rev-parse", "HEAD") == previous
    assert b"<title>Logos</title>" in page(env)
    assert (state / "logos.memory").read_bytes() == b"existing-persistent-data"
    assert (state / "provider-encryption.key").read_text() == "fixture-encryption-key"


def test_delayed_job_deploys_current_develop_not_old_event_sha(deployment):
    app, _, env, _, author = deployment
    env["GITHUB_SHA"] = git(app, "rev-parse", "HEAD")
    target = publish(author)
    result = run("deploy", env)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["app_revision"] == target != env["GITHUB_SHA"]
    assert b"deployed-v2" in page(env)


def test_automatic_deployment_refuses_other_branches(deployment):
    app, state, env, _, author = deployment
    initial = json.loads(run("start", env).stdout)
    publish(author)
    env["GITHUB_REF"] = "refs/heads/other"
    result = run("deploy", env)
    assert result.returncode == 2 and "only this repository develop" in result.stderr
    assert json.loads(run("status", env).stdout)["pid"] == initial["pid"]
    assert (state / "launches").read_text().count("start") == 1


@pytest.mark.parametrize("corrupt", [False, True])
def test_deploy_materializes_verified_lfs_bytes(deployment, corrupt):
    app, state, env, _, author = deployment
    initial = json.loads(run("start", env).stdout)
    previous = git(app, "rev-parse", "HEAD")
    data = b"actual-lfs-runtime-asset"
    oid = hashlib.sha256(data).hexdigest()
    (author / ".gitattributes").write_text("asset.dat filter=lfs diff=lfs merge=lfs -text\n")
    (author / "asset.dat").write_text(f"version https://git-lfs.github.com/spec/v1\noid sha256:{oid}\nsize {len(data)}\n")
    git(author, "add", ".gitattributes", "asset.dat")
    git(author, "commit", "-qm", "publish LFS pointer")
    git(author, "push", "-q", "origin", "develop")
    cached = app / ".git/lfs/objects" / oid[:2] / oid[2:4] / oid
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(b"x" * len(data) if corrupt else data)
    result = run("deploy", env)
    if corrupt:
        assert result.returncode != 0
        assert git(app, "rev-parse", "HEAD") == previous
        assert json.loads(run("status", env).stdout)["pid"] == initial["pid"]
    else:
        assert result.returncode == 0, result.stderr
        assert (app / "asset.dat").read_bytes() == data
        assert "Git LFS is not installed" not in result.stderr


def test_workflow_deploys_every_develop_push_in_one_serial_shell_step():
    import yaml
    workflow = yaml.safe_load((SCRIPT.parents[1] / ".github/workflows/redmi9-app-server.yml").read_text())
    events = workflow.get("on", workflow.get(True))
    assert events["push"] == {"branches": ["develop"]}
    assert "workflow_dispatch" in events
    assert workflow["concurrency"]["cancel-in-progress"] is False
    job = workflow["jobs"]["server"]
    assert job["runs-on"] == "redmi9" and len(job["steps"]) == 1
    assert job["steps"][0]["name"] == "logos"
    assert job["steps"][0]["env"]["LOGOS_REQUIRE_WIFI"] == "1"
    assert "bash scripts/redmi_app_server.sh deploy" in job["steps"][0]["run"]

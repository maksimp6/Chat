import subprocess
import threading
from types import SimpleNamespace

import pytest

import db
import environment_manager
from environment_manager import (
    create_environment,
    delete_environment,
    dispatch_environment_revision,
    init_environment_tables,
    list_environments,
    start_environment,
    stop_environment,
)
from environment_routes import environment_bp
from flask import Flask


def _git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "master"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "ci@example.test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "CI"], cwd=repo, check=True)
    (repo / "marker.txt").write_text("master\\n", encoding="utf-8")
    _write_runtime(repo, "master")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "master"], cwd=repo, check=True, capture_output=True)
    master_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    subprocess.run(
        ["git", "switch", "-c", "feature/one"], cwd=repo, check=True, capture_output=True
    )
    (repo / "marker.txt").write_text("feature-one\\n", encoding="utf-8")
    _write_runtime(repo, "one")
    subprocess.run(
        ["git", "commit", "-am", "feature one"], cwd=repo, check=True, capture_output=True
    )
    one_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    subprocess.run(
        ["git", "switch", "-c", "feature/two"], cwd=repo, check=True, capture_output=True
    )
    (repo / "marker.txt").write_text("feature-two\\n", encoding="utf-8")
    _write_runtime(repo, "two")
    subprocess.run(
        ["git", "commit", "-am", "feature two"], cwd=repo, check=True, capture_output=True
    )
    two_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    return repo, master_sha, one_sha, two_sha


def _write_runtime(repo, revision):
    (repo / "alice_runtime.py").write_text(
        f'''STATE = []
class Application:
    def invoke(self, operation, payload):
        STATE.append(payload["value"])
        return {{"revision": "{revision}", "state": list(STATE)}}
def create_runtime(host):
    return Application()
''',
        encoding="utf-8",
    )


def _setup(tmp_path, monkeypatch):
    repo, master_sha, one_sha, two_sha = _git_repo(tmp_path)
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "alice.db"))
    monkeypatch.setenv("ALICE_ENV_REPO_ROOT", str(repo))
    monkeypatch.setenv("ALICE_ENV_RUNTIME_ROOT", str(tmp_path / "runtimes"))
    db.init_db()
    init_environment_tables()
    return repo, master_sha, one_sha, two_sha


def test_two_branch_environments_are_immutable_and_isolated(tmp_path, monkeypatch):
    _, _, one_sha, two_sha = _setup(tmp_path, monkeypatch)
    first = create_environment("feature/one")
    second = create_environment("feature/two")
    assert first["commit_sha"] == one_sha
    assert second["commit_sha"] == two_sha
    assert first["environment_id"] != second["environment_id"]
    assert first["data_namespace"] != second["data_namespace"]
    assert first["url"] != second["url"]

    first = start_environment(first["environment_id"])
    second = start_environment(second["environment_id"])
    assert first["status"] == "RUNNING"
    assert second["status"] == "RUNNING"
    assert first["runtime_pid"] is None
    assert second["runtime_pid"] is None
    assert first["runtime_port"] is None
    assert second["runtime_port"] is None
    assert first["runtime_thread_id"] is not None
    assert second["runtime_thread_id"] is not None
    assert first["runtime_thread_id"] != second["runtime_thread_id"]
    assert first["runtime_thread_id"] != threading.get_ident()
    assert second["runtime_thread_id"] != threading.get_ident()

    barrier = threading.Barrier(2)
    results = {}

    def invoke(name, environment_id, value):
        barrier.wait()
        results[name] = dispatch_environment_revision(environment_id, "record", {"value": value})

    callers = [
        threading.Thread(target=invoke, args=("one", first["environment_id"], "first")),
        threading.Thread(target=invoke, args=("two", second["environment_id"], "second")),
    ]
    for caller in callers:
        caller.start()
    for caller in callers:
        caller.join()
    assert results == {
        "one": {"revision": "one", "state": ["first"]},
        "two": {"revision": "two", "state": ["second"]},
    }

    stop_environment(first["environment_id"])
    stop_environment(second["environment_id"])
    delete_environment(first["environment_id"])
    delete_environment(second["environment_id"])
    assert list_environments() == []


def test_environment_trace_contains_branch_commit_and_operation(tmp_path, monkeypatch):
    _, _, one_sha, _ = _setup(tmp_path, monkeypatch)
    item = create_environment("feature/one")
    conn = db.get_conn()
    event = conn.execute(
        "SELECT * FROM environment_events WHERE environment_id = ? ORDER BY created_at DESC LIMIT 1",
        (item["environment_id"],),
    ).fetchone()
    conn.close()
    assert event["operation"] == "CREATE_ENVIRONMENT"
    assert event["status"] == "SUCCESS"
    assert event["trace_id"]
    assert one_sha in event["trace_json"]
    assert "feature/one" in event["trace_json"]


def test_environment_api_exposes_lifecycle_contract(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    app = Flask(__name__)
    app.register_blueprint(environment_bp)
    client = app.test_client()
    created = client.post("/api/environments", json={"branch": "feature/one"})
    assert created.status_code == 201
    payload = created.get_json()
    environment_id = payload["environment_id"]
    assert client.get("/api/environments").status_code == 200
    assert client.get(f"/api/environments/{environment_id}").status_code == 200
    assert client.post(f"/api/environments/{environment_id}/start").status_code == 200
    assert client.post(f"/api/environments/{environment_id}/stop").status_code == 200
    assert client.post(f"/api/environments/{environment_id}/restart").status_code == 200
    assert client.delete(f"/api/environments/{environment_id}").status_code == 200
    assert client.get(f"/api/environments/{environment_id}").status_code == 404


def test_environment_init_recovers_stale_running_state_after_process_restart(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    item = create_environment("feature/one")
    conn = db.get_conn()
    conn.execute(
        "UPDATE environments SET status = ?, runtime_pid = ?, runtime_port = ? WHERE environment_id = ?",
        ("RUNNING", 12345, 54321, item["environment_id"]),
    )
    conn.commit()
    conn.close()

    init_environment_tables()

    recovered = next(
        environment
        for environment in list_environments()
        if environment["environment_id"] == item["environment_id"]
    )
    assert recovered["status"] == "STOPPED"
    assert recovered["runtime_pid"] is None
    assert recovered["runtime_port"] is None
    assert recovered["runtime_thread_id"] is None

    delete_environment(item["environment_id"])


def test_revision_dispatch_rejects_missing_runtime():
    with pytest.raises(RuntimeError, match="revision runtime is not loaded"):
        environment_manager._invoke_revision_runtime(
            SimpleNamespace(runtime_id="missing-runtime"),
            {"operation": "noop", "payload": {}},
        )


def _runtime_for_prepare_failure(tmp_path, monkeypatch, commit_sha="a" * 40):
    monkeypatch.setenv("ALICE_ENV_RUNTIME_ROOT", str(tmp_path / "runtimes"))
    runtime = environment_manager.EnvironmentRuntime(
        {
            "environment_id": "runtime-prepare-failure",
            "commit_sha": commit_sha,
            "data_namespace": "runtime-prepare-failure",
            "owner_id": None,
        }
    )
    runtime.worktree.mkdir(parents=True)
    return runtime


def test_runtime_prepare_rejects_wrong_commit(tmp_path, monkeypatch):
    expected = "a" * 40
    runtime = _runtime_for_prepare_failure(tmp_path, monkeypatch, expected)
    monkeypatch.setattr(environment_manager, "_run_git", lambda *args, **kwargs: "b" * 40)

    with pytest.raises(RuntimeError, match="does not match the resolved commit"):
        runtime._prepare()


def test_runtime_prepare_rejects_dirty_snapshot(tmp_path, monkeypatch):
    expected = "a" * 40
    runtime = _runtime_for_prepare_failure(tmp_path, monkeypatch, expected)

    def fake_git(*args, **kwargs):
        if args[:2] == ("rev-parse", "HEAD"):
            return expected
        if args[:2] == ("status", "--porcelain"):
            return " M alice_runtime.py"
        raise AssertionError(args)

    monkeypatch.setattr(environment_manager, "_run_git", fake_git)

    with pytest.raises(RuntimeError, match="not an immutable clean snapshot"):
        runtime._prepare()

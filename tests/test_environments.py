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


def _set_runtime_owner(environment_id, instance, heartbeat_at):
    conn = db.get_conn()
    conn.execute(
        """
        UPDATE environments
           SET status = ?, runtime_instance = ?, runtime_heartbeat_at = ?
         WHERE environment_id = ?
        """,
        ("RUNNING", instance, heartbeat_at, environment_id),
    )
    conn.commit()
    conn.close()


def _status(environment_id):
    return next(
        environment
        for environment in list_environments()
        if environment["environment_id"] == environment_id
    )


def test_environment_init_keeps_runtime_owned_by_live_instance(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    item = create_environment("feature/one")
    environment_id = item["environment_id"]
    _set_runtime_owner(environment_id, "other-host:1:abc", environment_manager._now())

    init_environment_tables()
    assert _status(environment_id)["status"] == "RUNNING"
    with pytest.raises(RuntimeError, match="running on another instance: other-host:1:abc"):
        dispatch_environment_revision(environment_id, "invoke", {"value": "x"})

    _set_runtime_owner(environment_id, "other-host:1:abc", environment_manager._now() - 3600)
    init_environment_tables()
    recovered = _status(environment_id)
    assert recovered["status"] == "STOPPED"
    assert recovered["runtime_instance"] is None
    assert recovered["runtime_heartbeat_at"] is None
    delete_environment(environment_id)


def test_started_environment_records_owner_instance_and_heartbeat(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    item = create_environment("feature/one")
    environment_id = item["environment_id"]
    started = start_environment(environment_id)
    try:
        assert started["runtime_instance"] == environment_manager.INSTANCE_ID
        stored = _status(environment_id)
        assert stored["runtime_instance"] == environment_manager.INSTANCE_ID

        conn = db.get_conn()
        conn.execute(
            "UPDATE environments SET runtime_heartbeat_at = 0 WHERE environment_id = ?",
            (environment_id,),
        )
        conn.commit()
        conn.close()
        environment_manager._record_heartbeat(environment_id)
        assert _status(environment_id)["runtime_heartbeat_at"] > 0
    finally:
        stopped = stop_environment(environment_id)
    assert stopped["runtime_instance"] is None
    assert _status(environment_id)["runtime_instance"] is None
    delete_environment(environment_id)


def test_environment_tables_add_runtime_owner_columns_to_old_schema(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "old.db"))
    conn = db.get_conn()
    conn.execute(
        """
        CREATE TABLE environments (
            environment_id TEXT PRIMARY KEY, branch_name TEXT NOT NULL,
            commit_sha TEXT NOT NULL, status TEXT NOT NULL, url TEXT,
            runtime_pid INTEGER, runtime_port INTEGER, data_namespace TEXT NOT NULL,
            owner_id TEXT, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
            started_at INTEGER, stopped_at INTEGER, deleted_at INTEGER, error TEXT
        )
        """
    )
    conn.commit()
    conn.close()

    init_environment_tables()

    conn = db.get_conn()
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(environments)")}
    conn.close()
    assert {"runtime_instance", "runtime_heartbeat_at"} <= columns


def test_environment_start_reserves_ownership_atomically(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    item = create_environment("feature/one")
    environment_id = item["environment_id"]
    stale = dict(_status(environment_id))
    # Another instance won the race after this one read STOPPED.
    _set_runtime_owner(environment_id, "other-host:1:abc", environment_manager._now())
    monkeypatch.setattr(environment_manager, "_require", lambda *args, **kwargs: dict(stale))
    with pytest.raises(ValueError, match="already being started by another instance"):
        start_environment(environment_id)
    assert _status(environment_id)["runtime_instance"] == "other-host:1:abc"


def test_environment_stop_rejects_runtime_on_another_live_instance(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    environment_id = create_environment("feature/one")["environment_id"]
    _set_runtime_owner(environment_id, "other-host:1:abc", environment_manager._now())
    with pytest.raises(ValueError, match="running on another instance: other-host:1:abc"):
        stop_environment(environment_id)
    assert _status(environment_id)["status"] == "RUNNING"


def test_local_runtime_that_lost_ownership_stops_serving(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    environment_id = create_environment("feature/one")["environment_id"]
    start_environment(environment_id)
    _set_runtime_owner(environment_id, "other-host:1:abc", environment_manager._now())
    with pytest.raises(RuntimeError, match="no longer owned by this instance"):
        dispatch_environment_revision(environment_id, "invoke", {"value": "x"})
    with environment_manager._RUNTIME_WORKERS_LOCK:
        assert environment_id not in environment_manager._RUNTIME_WORKERS


def test_environment_start_failure_releases_ownership(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    environment_id = create_environment("feature/one")["environment_id"]

    def broken_start(self):
        raise RuntimeError("worktree unavailable")

    monkeypatch.setattr(environment_manager.EnvironmentRuntime, "start", broken_start)
    with pytest.raises(RuntimeError, match="worktree unavailable"):
        start_environment(environment_id)
    failed = _status(environment_id)
    assert (failed["status"], failed["error"]) == ("FAILED", "worktree unavailable")
    assert failed["runtime_instance"] is None


def test_environment_init_reclaim_yields_to_a_fresh_heartbeat(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    environment_id = create_environment("feature/one")["environment_id"]
    _set_runtime_owner(environment_id, "other-host:1:abc", environment_manager._now() - 3600)
    real_owned = environment_manager._owned_by_live_instance

    def heartbeat_lands_after_read(row):
        # The owner heartbeats between the SELECT and the reclaim UPDATE.
        _set_runtime_owner(environment_id, "other-host:1:abc", environment_manager._now())
        return real_owned(row)

    monkeypatch.setattr(environment_manager, "_owned_by_live_instance", heartbeat_lands_after_read)
    init_environment_tables()
    assert _status(environment_id)["status"] == "RUNNING"
    assert _status(environment_id)["runtime_instance"] == "other-host:1:abc"


def test_environment_stop_yields_when_ownership_changed(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    environment_id = create_environment("feature/one")["environment_id"]
    _set_runtime_owner(environment_id, "new-host:1:abc", environment_manager._now())
    # What this instance read before a new owner reclaimed the dead one.
    stale = dict(_status(environment_id))
    stale.update(
        runtime_instance="dead-host:1:abc",
        runtime_heartbeat_at=environment_manager._now() - 3600,
    )
    monkeypatch.setattr(environment_manager, "_require", lambda *args, **kwargs: dict(stale))
    with pytest.raises(ValueError, match="ownership changed while stopping"):
        stop_environment(environment_id)
    assert _status(environment_id)["runtime_instance"] == "new-host:1:abc"


def test_local_runtime_whose_row_was_deleted_stops_serving(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    environment_id = create_environment("feature/one")["environment_id"]
    start_environment(environment_id)
    conn = db.get_conn()
    conn.execute("DELETE FROM environments WHERE environment_id = ?", (environment_id,))
    conn.commit()
    conn.close()
    with pytest.raises(RuntimeError, match="no longer owned by this instance"):
        dispatch_environment_revision(environment_id, "invoke", {"value": "x"})


def test_list_environments_filters_by_owner(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    mine = create_environment("feature/one", owner_id="alice")["environment_id"]
    create_environment("feature/one", owner_id="bob")
    assert [item["environment_id"] for item in list_environments("alice")] == [mine]

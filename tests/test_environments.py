import subprocess
import threading

import db
from environment_manager import (
    create_environment,
    delete_environment,
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
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "master"], cwd=repo, check=True, capture_output=True)
    master_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    subprocess.run(
        ["git", "switch", "-c", "feature/one"], cwd=repo, check=True, capture_output=True
    )
    (repo / "marker.txt").write_text("feature-one\\n", encoding="utf-8")
    subprocess.run(
        ["git", "commit", "-am", "feature one"], cwd=repo, check=True, capture_output=True
    )
    one_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    subprocess.run(
        ["git", "switch", "-c", "feature/two"], cwd=repo, check=True, capture_output=True
    )
    (repo / "marker.txt").write_text("feature-two\\n", encoding="utf-8")
    subprocess.run(
        ["git", "commit", "-am", "feature two"], cwd=repo, check=True, capture_output=True
    )
    two_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    return repo, master_sha, one_sha, two_sha


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

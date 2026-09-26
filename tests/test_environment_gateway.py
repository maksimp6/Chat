import subprocess
import threading

import db
from flask import Flask, Response, jsonify

from environment_manager import (
    create_environment,
    delete_environment,
    init_environment_tables,
    start_environment,
    stop_environment,
)
from environment_routes import environment_bp, environment_gateway_bp
from runtime import current_runtime_base_path, current_runtime_id


def _setup_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "master"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "ci@example.test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "CI"], cwd=repo, check=True)
    (repo / "marker.txt").write_text("runtime\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "runtime"], cwd=repo, check=True, capture_output=True)
    return repo


def _app():
    app = Flask(__name__)

    @app.get("/runtime-root")
    def runtime_root():
        return jsonify(
            {
                "runtime_id": current_runtime_id(),
                "base_path": current_runtime_base_path(),
                "thread_id": threading.get_ident(),
            }
        )

    @app.get("/runtime-stream")
    def runtime_stream():
        def generate():
            yield b"first-"
            yield b"second"

        return Response(generate(), mimetype="text/plain")

    app.register_blueprint(environment_bp)
    app.register_blueprint(environment_gateway_bp)
    return app


def test_environment_gateway_runs_request_in_matching_runtime_thread(tmp_path, monkeypatch):
    repo = _setup_repo(tmp_path)
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "alice.db"))
    monkeypatch.setenv("ALICE_ENV_REPO_ROOT", str(repo))
    monkeypatch.setenv("ALICE_ENV_RUNTIME_ROOT", str(tmp_path / "runtimes"))
    db.init_db()
    init_environment_tables()

    app = _app()
    client = app.test_client()
    first = create_environment("master")
    second = create_environment("master")
    first = start_environment(first["environment_id"])
    second = start_environment(second["environment_id"])

    try:
        response = client.get(
            f"/environments/{first['environment_id']}/runtime-root?hello=world"
        )
        assert response.status_code == 200
        payload = response.get_json()
        assert payload["runtime_id"] == first["environment_id"]
        assert payload["base_path"] == f"/environments/{first['environment_id']}"
        assert payload["thread_id"] == first["runtime_thread_id"]
        assert payload["thread_id"] != threading.get_ident()
        assert first["runtime_thread_id"] != second["runtime_thread_id"]
    finally:
        stop_environment(first["environment_id"])
        stop_environment(second["environment_id"])
        delete_environment(first["environment_id"])
        delete_environment(second["environment_id"])


def test_environment_gateway_preserves_streaming_from_runtime_thread(tmp_path, monkeypatch):
    repo = _setup_repo(tmp_path)
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "alice.db"))
    monkeypatch.setenv("ALICE_ENV_REPO_ROOT", str(repo))
    monkeypatch.setenv("ALICE_ENV_RUNTIME_ROOT", str(tmp_path / "runtimes"))
    db.init_db()
    init_environment_tables()

    app = _app()
    client = app.test_client()
    created = create_environment("master")
    started = start_environment(created["environment_id"])

    try:
        response = client.get(
            f"/environments/{created['environment_id']}/runtime-stream"
        )
        assert response.status_code == 200
        assert response.data == b"first-second"
    finally:
        stop_environment(started["environment_id"])
        delete_environment(started["environment_id"])


def test_environment_gateway_rejects_stopped_runtime_without_transport(tmp_path, monkeypatch):
    repo = _setup_repo(tmp_path)
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "alice.db"))
    monkeypatch.setenv("ALICE_ENV_REPO_ROOT", str(repo))
    monkeypatch.setenv("ALICE_ENV_RUNTIME_ROOT", str(tmp_path / "runtimes"))
    db.init_db()
    init_environment_tables()

    app = _app()
    client = app.test_client()
    created = create_environment("master")
    response = client.get(
        f"/environments/{created['environment_id']}/runtime-root"
    )
    assert response.status_code == 503
    assert response.get_json() == {"error": "environment_not_running"}
    delete_environment(created["environment_id"])

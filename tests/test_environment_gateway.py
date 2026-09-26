import subprocess
import time

import db
import requests
from flask import Flask

from environment_manager import (
    create_environment,
    delete_environment,
    init_environment_tables,
    start_environment,
    stop_environment,
)
from environment_routes import environment_bp, environment_gateway_bp


def test_environment_url_routes_to_the_matching_runtime(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "master"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "ci@example.test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "CI"], cwd=repo, check=True)
    (repo / "app.py").write_text(
        "from flask import Flask, jsonify\n"
        "import os\n"
        "app=Flask(__name__)\n"
        "@app.get('/')\n"
        "def root(): return jsonify({'environment': True})\n"
        "app.run(host='127.0.0.1', port=int(os.environ['PORT']))\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "runtime"], cwd=repo, check=True, capture_output=True)

    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "alice.db"))
    monkeypatch.setenv("ALICE_ENV_REPO_ROOT", str(repo))
    monkeypatch.setenv("ALICE_ENV_RUNTIME_ROOT", str(tmp_path / "runtimes"))
    monkeypatch.setenv("ALICE_ENV_RUNTIME_COMMAND", "python app.py")
    db.init_db()
    init_environment_tables()

    app = Flask(__name__)
    app.register_blueprint(environment_bp)
    app.register_blueprint(environment_gateway_bp)
    client = app.test_client()

    created = create_environment("master")
    started = start_environment(created["environment_id"])
    port = started["runtime_port"]

    for _ in range(30):
        try:
            if requests.get("http://127.0.0.1:" + str(port) + "/", timeout=1).ok:
                break
        except requests.RequestException:
            time.sleep(0.1)
    else:
        raise AssertionError("runtime did not become ready")

    response = client.get("/environments/" + created["environment_id"] + "/")
    assert response.status_code == 200
    assert response.get_json() == {"environment": True}

    stop_environment(created["environment_id"])
    delete_environment(created["environment_id"])

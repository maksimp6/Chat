import subprocess

import pytest

import db
import environment_manager
from cloud.cloudru.environment_adapter import CloudRuEnvironmentAdapter
from environment_manager import (
    LocalEnvironmentAdapter,
    _adapter_for,
    _default_ttl_seconds,
    _resolve_ttl_seconds,
    _validate_adapter_name,
    cleanup_expired_environments,
    create_environment,
    execute_environment,
    init_environment_tables,
    list_environments,
)


def _git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "master"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "ci@example.test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "CI"], cwd=repo, check=True)
    (repo / "marker.txt").write_text("master\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "master"], cwd=repo, check=True, capture_output=True)
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    return repo, sha


def _setup(tmp_path, monkeypatch):
    repo, sha = _git_repo(tmp_path)
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "alice.db"))
    monkeypatch.setenv("ALICE_ENV_REPO_ROOT", str(repo))
    monkeypatch.setenv("ALICE_ENV_RUNTIME_ROOT", str(tmp_path / "runtimes"))
    monkeypatch.delenv("ALICE_ENV_ADAPTER", raising=False)
    monkeypatch.delenv("ALICE_ENV_DEFAULT_TTL_SECONDS", raising=False)
    monkeypatch.delenv("ALICE_CLOUDRU_ENV_DEFAULT_TTL_SECONDS", raising=False)
    db.init_db()
    init_environment_tables()
    return repo, sha


def test_validate_adapter_name_accepts_known_values_and_rejects_others():
    assert _validate_adapter_name(None) == "local"
    assert _validate_adapter_name("Local") == "local"
    assert _validate_adapter_name("cloudru") == "cloudru"
    with pytest.raises(ValueError, match="Unknown environment adapter"):
        _validate_adapter_name("ec2")


def test_adapter_for_selects_implementation_by_adapter_field():
    local_env = {"environment_id": "e1", "adapter": "local"}
    cloud_env = {"environment_id": "e2", "adapter": "cloudru", "commit_sha": "a" * 40}
    assert isinstance(_adapter_for(local_env), LocalEnvironmentAdapter)
    assert isinstance(_adapter_for(cloud_env), CloudRuEnvironmentAdapter)


def test_ttl_defaults_differ_between_local_and_cloudru(monkeypatch):
    monkeypatch.delenv("ALICE_ENV_DEFAULT_TTL_SECONDS", raising=False)
    monkeypatch.delenv("ALICE_CLOUDRU_ENV_DEFAULT_TTL_SECONDS", raising=False)
    assert _default_ttl_seconds("local") is None
    assert _default_ttl_seconds("cloudru") == 3600

    monkeypatch.setenv("ALICE_ENV_DEFAULT_TTL_SECONDS", "120")
    monkeypatch.setenv("ALICE_CLOUDRU_ENV_DEFAULT_TTL_SECONDS", "900")
    assert _default_ttl_seconds("local") == 120
    assert _default_ttl_seconds("cloudru") == 900

    assert _resolve_ttl_seconds("local", 45) == 45
    with pytest.raises(ValueError, match="ttl_seconds must be > 0"):
        _resolve_ttl_seconds("local", 0)


class FakeAdapter:
    instances: list["FakeAdapter"] = []

    def __init__(self, environment):
        self.environment = environment
        self.provisioned = False
        self.started = False
        self.stopped = False
        self.removed = False
        FakeAdapter.instances.append(self)

    def provision(self):
        self.provisioned = True
        return {"cloud_resource_id": "sandbox-123"}

    def start(self):
        self.started = True
        return {}

    def execute(self, command, *, timeout_seconds=30.0):
        return {"success": True, "stdout": f"ran: {command}", "stderr": "", "exit_code": 0}

    def stop(self):
        self.stopped = True

    def remove(self):
        self.removed = True


class FailingProvisionAdapter(FakeAdapter):
    def provision(self):
        raise RuntimeError("Cloud.ru quota exceeded")


def test_create_environment_persists_adapter_ttl_and_cloud_resource_id(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    FakeAdapter.instances.clear()
    monkeypatch.setattr(environment_manager, "_adapter_for", lambda item: FakeAdapter(item))

    item = create_environment("master", adapter="cloudru", ttl_seconds=120)

    assert item["adapter"] == "cloudru"
    assert item["ttl_seconds"] == 120
    assert item["cloud_resource_id"] == "sandbox-123"
    assert item["status"] == "STOPPED"
    assert item["expires_at"] is not None
    assert FakeAdapter.instances[0].provisioned is True

    listed = next(e for e in list_environments() if e["environment_id"] == item["environment_id"])
    assert listed["adapter"] == "cloudru"
    assert listed["cloud_resource_id"] == "sandbox-123"


def test_create_environment_marks_failed_when_provision_raises(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(
        environment_manager, "_adapter_for", lambda item: FailingProvisionAdapter(item)
    )

    with pytest.raises(RuntimeError, match="Cloud.ru quota exceeded"):
        create_environment("master", adapter="cloudru")

    conn = db.get_conn()
    row = conn.execute("SELECT status, error FROM environments ORDER BY created_at DESC LIMIT 1").fetchone()
    conn.close()
    assert row["status"] == "FAILED"
    assert "quota exceeded" in row["error"]


def test_execute_environment_requires_running_status(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(environment_manager, "_adapter_for", lambda item: FakeAdapter(item))
    item = create_environment("master", adapter="cloudru")

    with pytest.raises(ValueError, match="environment is not running"):
        execute_environment(item["environment_id"], "echo hi")


def test_execute_environment_runs_command_when_running(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(environment_manager, "_adapter_for", lambda item: FakeAdapter(item))
    item = create_environment("master", adapter="cloudru")
    environment_manager.start_environment(item["environment_id"])

    result = execute_environment(item["environment_id"], "echo hi")

    assert result["success"] is True
    assert result["stdout"] == "ran: echo hi"


def test_cleanup_expired_environments_deletes_only_past_ttl(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(environment_manager, "_adapter_for", lambda item: FakeAdapter(item))

    fresh = create_environment("master", adapter="cloudru", ttl_seconds=3600)
    expired = create_environment("master", adapter="cloudru", ttl_seconds=60)

    conn = db.get_conn()
    conn.execute(
        "UPDATE environments SET expires_at = ? WHERE environment_id = ?",
        (environment_manager._now() - 10, expired["environment_id"]),
    )
    conn.commit()
    conn.close()

    removed = cleanup_expired_environments()

    assert removed == [expired["environment_id"]]
    remaining_ids = {e["environment_id"] for e in list_environments()}
    assert fresh["environment_id"] in remaining_ids
    assert expired["environment_id"] not in remaining_ids


def test_cleanup_expired_environments_scoped_to_owner(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(environment_manager, "_adapter_for", lambda item: FakeAdapter(item))

    mine = create_environment("master", owner_id="owner-a", adapter="cloudru", ttl_seconds=60)
    other = create_environment("master", owner_id="owner-b", adapter="cloudru", ttl_seconds=60)
    conn = db.get_conn()
    conn.execute(
        "UPDATE environments SET expires_at = ?",
        (environment_manager._now() - 10,),
    )
    conn.commit()
    conn.close()

    removed = cleanup_expired_environments("owner-a")

    assert removed == [mine["environment_id"]]
    assert environment_manager._get(other["environment_id"]) is not None


def test_cleanup_expired_environments_skips_rows_deleted_concurrently(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(environment_manager, "_adapter_for", lambda item: FakeAdapter(item))
    expired = create_environment("master", adapter="cloudru", ttl_seconds=60)
    conn = db.get_conn()
    conn.execute(
        "UPDATE environments SET expires_at = ? WHERE environment_id = ?",
        (environment_manager._now() - 10, expired["environment_id"]),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(
        environment_manager,
        "delete_environment",
        lambda *a, **k: (_ for _ in ()).throw(KeyError("environment_not_found")),
    )

    assert cleanup_expired_environments() == []


def test_execute_environment_records_failure_and_reraises(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)

    class BrokenAdapter(FakeAdapter):
        def execute(self, command, *, timeout_seconds=30.0):
            raise RuntimeError("sandbox unreachable")

    monkeypatch.setattr(environment_manager, "_adapter_for", lambda item: BrokenAdapter(item))
    item = create_environment("master", adapter="cloudru")
    environment_manager.start_environment(item["environment_id"])

    with pytest.raises(RuntimeError, match="sandbox unreachable"):
        execute_environment(item["environment_id"], "boom")


def test_execute_environment_records_command_failure(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)

    class FailingCommandAdapter(FakeAdapter):
        def execute(self, command, *, timeout_seconds=30.0):
            return {"success": False, "stdout": "", "stderr": "boom", "exit_code": 1}

    monkeypatch.setattr(environment_manager, "_adapter_for", lambda item: FailingCommandAdapter(item))
    item = create_environment("master", adapter="cloudru")
    environment_manager.start_environment(item["environment_id"])

    result = execute_environment(item["environment_id"], "false")
    assert result["success"] is False
    assert result["exit_code"] == 1


def test_local_adapter_create_start_execute_stop_delete_round_trip(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    item = create_environment("master")
    assert item["adapter"] == "local"
    assert item["ttl_seconds"] is None
    assert item["expires_at"] is None

    started = environment_manager.start_environment(item["environment_id"])
    assert started["status"] == "RUNNING"

    result = execute_environment(item["environment_id"], "echo hello")
    assert result["success"] is True
    assert result["stdout"].strip() == "hello"

    environment_manager.stop_environment(item["environment_id"])
    environment_manager.delete_environment(item["environment_id"])
    assert list_environments() == []

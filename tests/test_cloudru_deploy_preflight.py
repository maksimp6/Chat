"""Native deployment storage configuration stays fail-closed and secret-safe."""

from __future__ import annotations
import importlib
import inspect
import json
from pathlib import Path
import pytest
from kristall import Kristall
from scripts import cloudru_deploy_preflight as preflight


@pytest.fixture
def inputs(tmp_path):
    config = json.loads(
        (Path(__file__).parents[1] / "deploy/cloudru/deployment.example.json").read_text()
    )
    config.update(
        project_id="aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
        source_commit="a" * 40,
        image="registry.test/alice-pro@sha256:" + "b" * 64,
        public_origin="https://alice.test",
        workflow_application_id="bbbbbbbb-cccc-4ddd-8eee-ffffffffffff",
    )
    root = tmp_path / "data"
    root.mkdir()
    path = root / "alice.memory"
    config["database"].update(
        path=str(path),
        data_root=str(root),
        persistence_evidence="synthetic fixture, no infrastructure claim",
        restore_evidence="isolated native restore test fixture",
    )
    env = {
        "ALICE_REQUIRE_SHORT_TOKEN": "1",
        "ALICE_SHORT_TOKEN": "test-short",
        "ALICE_PROVIDER_CREDENTIAL_KEY": "test-credential",
        "ALICE_GITHUB_CLIENT_ID": "test-client",
        "ALICE_GITHUB_CLIENT_SECRET": "test-secret",
        "ALICE_GITHUB_ALLOWED_IDS": "293531601",
        "ALICE_GITHUB_REDIRECT_URI": "https://alice.test/auth/github/callback",
        "ALICE_MEMORY_PATH": str(path),
        "EDS_API_KEY": "test-eds",
        "EDS_PROJECT_ID": config["project_id"],
    }
    return config, env


def test_valid_inputs_need_no_network(inputs):
    config, env = inputs
    assert preflight.validate(config, env, workflow=True) == []


@pytest.mark.parametrize(
    "value", ["relative.memory", "/outside/alice.memory", "/data/../escape", "bad\x00path", ""]
)
def test_rejects_unsafe_journal_paths(inputs, value):
    config, env = inputs
    env["ALICE_MEMORY_PATH"] = value
    assert preflight.validate(config, env)


@pytest.mark.parametrize(
    "field,value",
    [
        ("engine", "other"),
        ("engine", "memory_engine.MemoryStore"),
        ("writer_processes", 2),
        ("writer_processes", True),
        ("data_root", "relative"),
        ("persistence_evidence", ""),
        ("restore_evidence", ""),
    ],
)
def test_storage_contract_is_explicit(inputs, field, value):
    config, env = inputs
    config["database"][field] = value
    assert preflight.validate(config, env)


def test_template_engine_resolves_to_implemented_native_class():
    repo = Path(__file__).parents[1].resolve()
    template = json.loads((repo / "deploy/cloudru/deployment.example.json").read_text())
    module_name, _, class_name = template["database"]["engine"].rpartition(".")
    assert module_name and class_name
    resolved = getattr(importlib.import_module(module_name), class_name)
    assert inspect.isclass(resolved)
    assert resolved is Kristall
    assert Path(inspect.getfile(resolved)).resolve().is_relative_to(repo)


def test_retired_sql_selector_is_rejected_without_echoing_secret(inputs):
    config, env = inputs
    env["ALICE_DATABASE_URL"] = "postgresql://user:secret-marker@obsolete/db"
    errors = preflight.validate(config, env)
    assert errors and "secret-marker" not in json.dumps(errors)


@pytest.mark.parametrize(
    "field,value",
    [
        ("ALICE_REQUIRE_SHORT_TOKEN", "0"),
        ("ALICE_GITHUB_ALLOWED_IDS", ""),
        ("ALICE_GITHUB_CLIENT_SECRET", ""),
        ("ALICE_PROVIDER_CREDENTIAL_KEY", "REPLACE_SECRET"),
        ("ALICE_GITHUB_REDIRECT_URI", "https://wrong.test/callback"),
    ],
)
def test_rejects_broken_auth_and_secret_configuration(inputs, field, value):
    config, env = inputs
    env[field] = value
    assert preflight.validate(config, env)


def test_rejects_template_without_echoing_input():
    template = json.loads(
        (Path(__file__).parents[1] / "deploy/cloudru/deployment.example.json").read_text()
    )
    errors = preflight.validate(template, {"ALICE_MEMORY_PATH": "secret-marker"})
    assert errors and "secret-marker" not in json.dumps(errors)


def test_workflow_rejects_wrong_project_and_endpoint(inputs):
    config, env = inputs
    env.update(EDS_PROJECT_ID="wrong", EDS_WF_API_URL="https://untrusted.test")
    errors = preflight.validate(config, env, workflow=True)
    assert any("EDS_PROJECT_ID" in error for error in errors)
    assert any("endpoints" in error for error in errors)


def test_rejects_unbounded_scaling_and_mutable_image(inputs):
    config, env = inputs
    config["container"]["max_instances"] = 50
    config["image"] = "registry.test/alice:latest"
    errors = preflight.validate(config, env)
    assert any("container:" in error for error in errors)
    assert any("image:" in error for error in errors)


def test_probe_preserves_valid_journal_and_refuses_active_writer(inputs):
    config, env = inputs
    path = Path(env["ALICE_MEMORY_PATH"])
    with Kristall(path) as store:
        store.set("probe", {"value": 1})
        store.commit()
        before = path.read_bytes()
        assert preflight.probe_database(str(path), config["database"]) is not None
        assert path.read_bytes() == before
    assert preflight.probe_database(str(path), config["database"]) is None
    assert path.read_bytes() == before


def test_probe_rejects_missing_corrupt_or_symlink_source(inputs, tmp_path):
    config, env = inputs
    path = Path(env["ALICE_MEMORY_PATH"])
    assert preflight.probe_database(str(path), config["database"]) is not None
    path.write_bytes(b"corrupt secret-marker")
    assert "secret-marker" not in preflight.probe_database(str(path), config["database"])
    assert path.read_bytes() == b"corrupt secret-marker"
    path.unlink()
    outside = tmp_path / "outside.memory"
    outside.write_bytes(b"private")
    path.symlink_to(outside)
    assert preflight.probe_database(str(path), config["database"]) is not None
    assert outside.read_bytes() == b"private"


def test_secret_bearing_parse_error_is_not_printed(tmp_path, capsys):
    path = tmp_path / "broken.json"
    path.write_text('{"credential":"secret-marker" invalid}')
    assert preflight.main(["--config", str(path)]) == 1
    output = capsys.readouterr()
    assert "secret-marker" not in output.out + output.err
    assert json.loads(output.out)["status"] == "INVALID"


def test_success_does_not_claim_deployment_ready(inputs, tmp_path, monkeypatch, capsys):
    config, env = inputs
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    path = tmp_path / "deployment.json"
    path.write_text(json.dumps(config))
    monkeypatch.setattr(
        preflight, "probe_database", lambda *_args: pytest.fail("unexpected database access")
    )
    assert preflight.main(["--config", str(path)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "CONFIG_VALID" and report["deployment_ready"] is False


def test_explicit_probe_reports_only_verified_existing_source(
    inputs, tmp_path, monkeypatch, capsys
):
    config, env = inputs
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    with Kristall(env["ALICE_MEMORY_PATH"]) as store:
        store.set("value", 1)
        store.commit()
    path = tmp_path / "deployment.json"
    path.write_text(json.dumps(config))
    assert preflight.main(["--config", str(path), "--check-db"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["database_probe"] == "PASSED"
    assert report["deployment_ready"] is False

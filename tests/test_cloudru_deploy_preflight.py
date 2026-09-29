from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import cloudru_deploy_preflight as preflight


@pytest.fixture
def inputs():
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
    config["database"].update(
        host="db.test",
        allowed_client_cidrs=["8.8.8.8/32"],
        egress_evidence="synthetic test fixture; no infrastructure claim",
    )
    env = {
        "ALICE_REQUIRE_SHORT_TOKEN": "1",
        "ALICE_SHORT_TOKEN": "test-only-short-token",
        "ALICE_PROVIDER_CREDENTIAL_KEY": "test-only-credential-key",
        "ALICE_GITHUB_CLIENT_ID": "test-client-id",
        "ALICE_GITHUB_CLIENT_SECRET": "test-client-secret",
        "ALICE_GITHUB_ALLOWED_IDS": "293531601",
        "ALICE_GITHUB_REDIRECT_URI": "https://alice.test/auth/github/callback",
        "ALICE_DATABASE_URL": (
            "postgresql://alice_app:test-only-p%40ssword@db.test:5432/alice?"
            "sslmode=verify-full&sslrootcert=/tmp/test-ca.crt&connect_timeout=5&"
            "ssl_min_protocol_version=TLSv1.2&gssencmode=disable&channel_binding=require"
        ),
        "EDS_API_KEY": "test-eds-key",
        "EDS_PROJECT_ID": config["project_id"],
    }
    return config, env


def test_valid_inputs_need_no_network_or_ca_file(inputs):
    config, env = inputs
    assert preflight.validate(config, env, workflow=True) == []


@pytest.mark.parametrize(
    "change",
    [
        "sslmode=require",
        "sslmode=prefer",
        "sslmode=disable",
        "sslmode=verify-full&sslmode=disable",
        "sslmode=verify-full&host=evil.test",
        "sslmode=verify-full&service=unexpected",
        "sslmode=verify-full&options=secret-value",
    ],
)
def test_rejects_tls_downgrade_and_libpq_query_overrides(inputs, change):
    config, env = inputs
    env["ALICE_DATABASE_URL"] = env["ALICE_DATABASE_URL"].replace("sslmode=verify-full", change)
    errors = preflight.validate(config, env)
    assert errors
    assert "secret-value" not in json.dumps(errors)


@pytest.mark.parametrize("mutation", ["uppercase-scheme", "malformed-percent"])
def test_rejects_malformed_or_noncanonical_dsn(inputs, mutation):
    config, env = inputs
    dsn = env["ALICE_DATABASE_URL"]
    if mutation == "uppercase-scheme":
        dsn = dsn.replace("postgresql://", "POSTGRESQL://", 1)
    else:
        dsn = dsn.replace("p%40ssword", "p%zzssword", 1)
    env["ALICE_DATABASE_URL"] = dsn
    errors = preflight.validate(config, env)
    assert any("ALICE_DATABASE_URL" in error for error in errors)
    assert "p%zzssword" not in json.dumps(errors)


@pytest.mark.parametrize("location", ["password", "application_name", "raw-nul"])
def test_rejects_nul_bytes_in_dsn(inputs, location):
    config, env = inputs
    dsn = env["ALICE_DATABASE_URL"]
    if location == "password":
        dsn = dsn.replace("p%40ssword", "p%00ssword", 1)
    elif location == "application_name":
        dsn += "&application_name=%00"
    else:
        dsn = dsn.replace("p%40ssword", "p\x00ssword", 1)
    env["ALICE_DATABASE_URL"] = dsn
    errors = preflight.validate(config, env)
    assert any("ALICE_DATABASE_URL" in error for error in errors)
    assert "p%00ssword" not in json.dumps(errors)
    assert r"p\u0000ssword" not in json.dumps(errors)


@pytest.mark.parametrize(
    "cidrs",
    [
        [],
        ["0.0.0.0/0"],
        ["::/0"],
        ["8.0.0.0/8"],
        ["127.0.0.1/32"],
        ["10.0.0.0/24"],
        ["224.0.0.1/32"],
        ["ff0e::1/128"],
        [123],
    ],
)
def test_rejects_absent_or_unsafe_public_egress(inputs, cidrs):
    config, env = inputs
    config["database"]["allowed_client_cidrs"] = cidrs
    assert any("allowed_client_cidrs" in e for e in preflight.validate(config, env))


def test_rejects_template_without_echoing_input():
    template = json.loads(
        (Path(__file__).parents[1] / "deploy/cloudru/deployment.example.json").read_text()
    )
    errors = preflight.validate(template, {"ALICE_DATABASE_URL": "bad-dsn-with-secret"})
    assert errors
    assert "bad-dsn-with-secret" not in json.dumps(errors)


@pytest.mark.parametrize(
    "field,value",
    [
        ("ALICE_REQUIRE_SHORT_TOKEN", "0"),
        ("ALICE_GITHUB_ALLOWED_IDS", ""),
        ("ALICE_GITHUB_CLIENT_SECRET", ""),
        ("ALICE_PROVIDER_CREDENTIAL_KEY", "REPLACE_SECRET"),
        ("ALICE_GITHUB_REDIRECT_URI", "https://unrelated.test/auth/github/callback"),
    ],
)
def test_rejects_broken_auth_and_secret_configuration(inputs, field, value):
    config, env = inputs
    env[field] = value
    assert preflight.validate(config, env)


def test_workflow_rejects_wrong_project_and_endpoint(inputs):
    config, env = inputs
    env.update(EDS_PROJECT_ID="wrong", EDS_WF_API_URL="https://untrusted.test")
    errors = preflight.validate(config, env, workflow=True)
    assert any("EDS_PROJECT_ID" in e for e in errors)
    assert any("endpoints" in e for e in errors)


def test_rejects_unbounded_scaling_and_mutable_image(inputs):
    config, env = inputs
    config["container"]["max_instances"] = 50
    config["image"] = "registry.test/alice-pro:latest"
    errors = preflight.validate(config, env)
    assert any("container:" in e for e in errors)
    assert any("image:" in e for e in errors)


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
        preflight, "probe_database", lambda *args: pytest.fail("unexpected DB access")
    )
    assert preflight.main(["--config", str(path)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "CONFIG_VALID"
    assert report["deployment_ready"] is False


def test_driver_failure_does_not_leak_dsn(inputs, monkeypatch):
    config, env = inputs
    monkeypatch.setattr(preflight.ssl, "create_default_context", lambda **kwargs: None)

    def fail(*args, **kwargs):
        raise RuntimeError("sensitive server error " + env["ALICE_DATABASE_URL"])

    monkeypatch.setitem(sys.modules, "psycopg", SimpleNamespace(connect=fail))
    error = preflight.probe_database(env["ALICE_DATABASE_URL"], config["database"])
    assert error and "postgresql://" not in error and "test-only" not in error


@pytest.mark.parametrize(
    "tls,role_attributes,has_role_membership",
    [
        (False, (False, False, False, False, False), False),
        (True, (True, False, False, False, False), False),
        (True, (False, True, False, False, False), False),
        (True, (False, False, True, False, False), False),
        (True, (False, False, False, False, False), True),
    ],
)
def test_probe_is_read_only_and_rejects_role_attributes_or_membership(
    inputs, monkeypatch, tls, role_attributes, has_role_membership
):
    config, env = inputs
    captured = {}
    monkeypatch.setattr(preflight.ssl, "create_default_context", lambda **kwargs: None)

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, sql):
            captured["sql"] = sql
            return SimpleNamespace(
                fetchone=lambda: (
                    "alice",
                    "alice_app",
                    tls,
                    "TLSv1.3",
                    *role_attributes,
                    has_role_membership,
                )
            )

    def connect(*args, **kwargs):
        captured.update(kwargs)
        return Connection()

    monkeypatch.setitem(sys.modules, "psycopg", SimpleNamespace(connect=connect))
    error = preflight.probe_database(env["ALICE_DATABASE_URL"], config["database"])
    assert (error is None) == (tls and not any(role_attributes) and not has_role_membership)
    assert "default_transaction_read_only=on" in captured["options"]
    assert "statement_timeout=5000" in captured["options"]
    assert captured["sql"].startswith("SELECT ")
    assert "pg_has_role" in captured["sql"]
    assert "FROM pg_roles p WHERE p.oid <> r.oid" in captured["sql"]
    assert "p.rolname <> 'pg_database_owner'" in captured["sql"]
    assert "p.rolname IN" not in captured["sql"]
    assert captured["connect_timeout"] == 5

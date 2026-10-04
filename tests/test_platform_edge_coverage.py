"""Focused edge coverage for Alice Platform foundation."""

import argparse
import runpy
import sys
from unittest.mock import patch

import pytest

from alice_platform.__main__ import cmd_health, cmd_validate, main
from alice_platform.config import (
    ConfigError,
    load_config,
    validate_no_cycles,
    validate_no_http_in_production,
    validate_no_plaintext_secrets,
    validate_schema,
    validate_service_references,
    validate_lane_invariants,
    _is_secret_pattern,
)
from alice_platform.health import generate_health_plan
from alice_platform.planner import Action, ActionType, plan_actions
from alice_platform.providers.cloudru import (
    CloudProviderUnavailable,
    create_container,
    delete_container,
    update_container,
)
from alice_platform.reconciler import reconcile


def _args(**values):
    return argparse.Namespace(**values)


def test_cli_error_and_empty_paths(tmp_path, capsys):
    missing = tmp_path / "missing"
    assert cmd_validate(_args(config_dir=str(missing))) == 1
    assert "Config error" in capsys.readouterr().err

    with patch("alice_platform.__main__.load_config", side_effect=RuntimeError("boom")):
        assert cmd_validate(_args(config_dir="config/alice")) == 1
        assert "Error: boom" in capsys.readouterr().err

    with (
        patch("alice_platform.__main__.load_config", return_value={}),
        patch("alice_platform.__main__.generate_health_plan", return_value=[]),
    ):
        assert cmd_health(_args(lane="test", config_dir="config/alice")) == 0
        assert "no health checks" in capsys.readouterr().out

    with patch("alice_platform.__main__.load_config", side_effect=ConfigError("bad")):
        assert cmd_health(_args(lane="test", config_dir="config/alice")) == 1
    with patch("alice_platform.__main__.load_config", side_effect=RuntimeError("boom")):
        assert cmd_health(_args(lane="test", config_dir="config/alice")) == 1


@pytest.mark.parametrize("command", ["plan", "reconcile"])
def test_platform_cli_does_not_expose_unobserved_plan_or_reconcile(command):
    with patch("sys.argv", ["alice_platform", command, "test"]):
        with pytest.raises(SystemExit) as exc:
            main()
    assert exc.value.code == 2


def test_module_main_exit_path():
    old = sys.argv[:]
    try:
        sys.argv = ["alice_platform"]
        with pytest.raises(SystemExit) as exc:
            runpy.run_module("alice_platform.__main__", run_name="__main__")
        assert exc.value.code == 1
    finally:
        sys.argv = old


def test_config_exported_helpers_and_invalid_shapes(tmp_path):
    assert _is_secret_pattern(123) is False
    assert _is_secret_pattern("plain-short-value") is False
    validate_lane_invariants({"ignored": "not-a-dict"})

    validate_no_plaintext_secrets({"x": 1, "items": [{"nested": "ok"}]})
    with pytest.raises(ConfigError, match="Invalid secret ref"):
        validate_no_plaintext_secrets({"secret": {"ref": "wrong/ref"}})

    validate_no_cycles({"a": {"depends_on": []}})
    with pytest.raises(ConfigError, match="must be a list"):
        validate_no_cycles({"a": {"depends_on": "b"}})

    validate_schema({"services": {}})
    with pytest.raises(ConfigError, match="Unknown field"):
        validate_schema({"services": {}, "wat": 1})
    with pytest.raises(ConfigError, match="dictionary"):
        validate_schema({"services": []})
    with pytest.raises(ConfigError, match="config must be a dictionary"):
        validate_schema({"services": {"x": "bad"}})

    validate_no_http_in_production({}, {})
    with pytest.raises(ConfigError, match="HTTP"):
        validate_no_http_in_production(
            {"x.test": {"service": "x", "protocol": "http"}},
            {"production": {"services": ["x"], "sign_in": "github"}},
        )

    validate_service_references({"ignored": "not-a-dict"}, {}, {"test": "not-a-dict"})
    with pytest.raises(ConfigError, match="not deployed"):
        validate_service_references({}, {"x": {"type": "x"}}, {"test": {"services": "x"}})

    with pytest.raises(ConfigError, match="Missing required file"):
        load_config(tmp_path / "none")


def test_health_and_planner_edge_branches():
    assert generate_health_plan("missing", {"lanes": {}}) == []

    config = {
        "domains": {
            "bad": "not-a-dict",
            "missing": {"protocol": "https"},
            "other.example": {"service": "other"},
            "svc.example": {"service": "svc"},
        },
        "lanes": {"test": {"services": ["svc"]}},
    }
    checks = generate_health_plan("test", config)
    assert len(checks) == 1
    assert checks[0].endpoint == "https://svc.example"
    assert checks[0].expected_sign_in == "passphrase"

    desired = {
        "services": {},
        "lanes": {"test": {"services": ["ghost"]}},
    }
    assert plan_actions("test", desired, {"containers": []}) == []


def test_cloudru_provider_fails_closed_and_reconciler_all_action_types(capsys):
    config = {"resources": {"cpu": "2", "memory": "1Gi", "gpu": "L4"}}
    for operation in (
        lambda: create_container("test", "svc", config),
        lambda: update_container("test", "svc", config),
        lambda: delete_container("test", "svc"),
    ):
        with pytest.raises(CloudProviderUnavailable):
            operation()

    actions = [
        Action(ActionType.CREATE, "create", "test", description="new"),
        Action(ActionType.UPDATE, "update", "test", description="change"),
        Action(ActionType.DELETE, "delete", "test"),
        Action(ActionType.REPORT_ORPHAN, "orphan", "test", description="old"),
        Action(ActionType.CREATE, "pending", "production", needs_approval=True),
    ]
    cfg = {"services": {name: {} for name in ["create", "update", "delete", "pending"]}}
    with (
        patch("alice_platform.reconciler.create_container") as create,
        patch("alice_platform.reconciler.update_container") as update,
        patch("alice_platform.reconciler.delete_container") as delete,
    ):
        reconcile("test", actions[:4], cfg)
        create.assert_called_once()
        update.assert_called_once()
        delete.assert_called_once()
    out = capsys.readouterr().out
    assert "[CREATE]" in out and "[UPDATE]" in out and "[DELETE]" in out and "[ORPHAN]" in out

    reconcile("production", [actions[-1]], cfg)
    assert "[PENDING]" in capsys.readouterr().out

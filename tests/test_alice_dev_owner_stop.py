"""Regression tests for the Alice Dev owner stop.

These tests must stay offline: the deploy entry point is inspected/executed without
provider imports, secret access, Docker, network calls, or resource deletion.
"""

from __future__ import annotations

import argparse
import ast
from pathlib import Path
import sys
from types import SimpleNamespace
from uuid import UUID

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts/cloudru_rdc_mcp_candidate.py"
WORKFLOW = ROOT / ".github/workflows/cloudru-rdc-mcp-candidate.yml"


class ProviderError(Exception):
    def __init__(self, message: str, *, code: str):
        super().__init__(message)
        self.code = code


@pytest.fixture
def cli():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
    names = {"main", "fail", "project_id", "candidate_name"}
    nodes = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    assert {node.name for node in nodes} == names
    calls = []
    values = {}

    def unexpected(name):
        def sentinel(*args, **kwargs):
            calls.append(name)
            raise AssertionError(f"Unexpected side-effect boundary: {name}")

        return sentinel

    namespace = {
        "__file__": str(SOURCE),
        "__doc__": "Offline owner-stop test",
        "argparse": argparse,
        "sys": sys,
        "Path": Path,
        "UUID": UUID,
        "os": SimpleNamespace(getenv=lambda key, default="": values.get(key, default)),
        "CloudProviderError": ProviderError,
    }
    for name in (
        "CloudRuContainerAppsClient",
        "purge_tombstones",
        "build_image",
        "create_candidate",
        "alice_dev_inventory",
    ):
        namespace[name] = unexpected(name)
    unit = ast.Module(body=nodes, type_ignores=[])
    exec(compile(unit, str(SOURCE), "exec"), namespace)
    return namespace, calls, values


@pytest.mark.parametrize(
    "extra",
    [
        {},
        {"ALICE_SHORT_TOKEN": "synthetic-only"},
        {
            "ALICE_SHORT_TOKEN": "synthetic-only",
            "ALICE_DEV_DEPLOY_ENABLED": "true",
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_RUN_ATTEMPT": "2",
        },
    ],
)
def test_valid_deploy_stops_without_provider_calls(cli, monkeypatch, extra):
    namespace, calls, values = cli
    values.update({"CLOUDRU_PROJECT_ID": "00000000-0000-0000-0000-000000000001"})
    values.update(extra)
    monkeypatch.setattr(sys, "argv", [str(SOURCE), "deploy", "--sha", "a" * 40])
    with pytest.raises(ProviderError) as caught:
        namespace["main"]()
    assert caught.value.code == "owner_stopped"
    assert calls == []


def test_stop_precedes_secret_lookup(cli, monkeypatch):
    namespace, calls, _ = cli

    def forbidden_lookup(*args):
        raise AssertionError("Environment/secret lookup happened before owner-stop")

    namespace["os"] = SimpleNamespace(getenv=forbidden_lookup)
    monkeypatch.setattr(sys, "argv", [str(SOURCE), "deploy", "--sha", "b" * 40])
    with pytest.raises(ProviderError) as caught:
        namespace["main"]()
    assert caught.value.code == "owner_stopped"
    assert calls == []


@pytest.mark.parametrize("sha", ["", "abc", "g" * 40])
def test_invalid_sha_still_fails_without_provider_calls(cli, monkeypatch, sha):
    namespace, calls, _ = cli
    monkeypatch.setattr(sys, "argv", [str(SOURCE), "deploy", "--sha", sha])
    with pytest.raises(ProviderError) as caught:
        namespace["main"]()
    assert caught.value.code == "validation_error"
    assert calls == []


def test_help_remains_available(cli, monkeypatch, capsys):
    namespace, calls, _ = cli
    monkeypatch.setattr(sys, "argv", [str(SOURCE), "--help"])
    with pytest.raises(SystemExit) as caught:
        namespace["main"]()
    assert caught.value.code == 0
    assert "--sha" in capsys.readouterr().out
    assert calls == []


def test_workflow_job_has_unconditional_owner_hold_before_steps():
    workflow = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert workflow["jobs"]["deploy"].get("if") == "${{ false }}"
    assert workflow["permissions"] == {"contents": "read"}


def test_distinct_commits_remain_distinct_candidates(cli):
    namespace, calls, _ = cli
    project = "00000000-0000-0000-0000-000000000001"
    first = namespace["candidate_name"](project, "a" * 40)
    second = namespace["candidate_name"](project, "b" * 40)
    assert first != second
    assert calls == []

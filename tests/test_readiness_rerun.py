"""Recovery must retry only the existing failed readiness check at current head."""

import copy
import importlib.util
import json
import runpy
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "scripts/readiness_rerun.py"
SPEC = importlib.util.spec_from_file_location("readiness_rerun", PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
HEAD = "a" * 40
REPO = "maksimp6/Chat"


def responses():
    pull = {
        "state": "open",
        "draft": False,
        "merged": False,
        "head": {"sha": HEAD, "repo": {"full_name": REPO}},
        "base": {"ref": "master", "sha": "b" * 40, "repo": {"full_name": REPO}},
    }
    run = {
        "path": ".github/workflows/merge-readiness.yml",
        "event": "pull_request",
        "head_sha": HEAD,
        "status": "completed",
        "repository": {"full_name": REPO},
        "pull_requests": [{"number": 7}],
    }
    jobs = {
        "total_count": 1,
        "jobs": [
            {
                "id": 99,
                "run_id": 8,
                "name": "Merge readiness snapshot",
                "status": "completed",
                "conclusion": "failure",
                "check_run_url": f"https://api.github.com/repos/{REPO}/check-runs/9",
            }
        ],
    }
    check = {"head_sha": HEAD, "name": "Merge readiness snapshot", "app": {"id": 15368}}
    return [pull, run, jobs, check, copy.deepcopy(pull)]


def bind(monkeypatch, data):
    queue = iter(data)
    monkeypatch.setattr(MODULE, "github_json", lambda *_: next(queue))


def test_current_failed_job_is_bound_to_existing_check(monkeypatch):
    bind(monkeypatch, responses())
    plan = MODULE.prepare_rerun(REPO, 7, 8, HEAD)
    assert plan["endpoint"] == f"repos/{REPO}/actions/jobs/99/rerun"
    assert plan["head_sha"] == HEAD


@pytest.mark.parametrize(
    "field,value",
    [
        ("state", "closed"),
        ("draft", True),
        ("merged", True),
        ("head.sha", "c" * 40),
        ("base.ref", "develop"),
        ("head.repo.full_name", "other/fork"),
        ("base.repo.full_name", "other/repo"),
    ],
)
def test_rejects_ineligible_pr(monkeypatch, field, value):
    data = responses()
    target = data[0]
    parts = field.split(".")
    for part in parts[:-1]:
        target = target[part]
    target[parts[-1]] = value
    bind(monkeypatch, data)
    with pytest.raises(ValueError):
        MODULE.prepare_rerun(REPO, 7, 8, HEAD)


@pytest.mark.parametrize(
    "index,field,value",
    [
        (1, "path", ".github/workflows/ci.yml"),
        (1, "event", "workflow_dispatch"),
        (1, "head_sha", "c" * 40),
        (1, "status", "in_progress"),
        (1, "pull_requests", [{"number": 42}]),
        (1, "repository", {"full_name": "other/repo"}),
        (2, "total_count", 101),
        (2, "jobs", []),
        (3, "head_sha", "c" * 40),
        (3, "app", {"id": 42}),
        (3, "name", "Fake readiness"),
    ],
)
def test_rejects_wrong_run_job_or_check(monkeypatch, index, field, value):
    data = responses()
    data[index][field] = value
    if index == 2 and field == "jobs":
        data[index]["total_count"] = 0
    bind(monkeypatch, data)
    with pytest.raises(ValueError):
        MODULE.prepare_rerun(REPO, 7, 8, HEAD)


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", True),
        ("id", 0),
        ("run_id", 99),
        ("status", "in_progress"),
        ("conclusion", "success"),
        ("conclusion", "skipped"),
        ("check_run_url", "https://evil.invalid/check-runs/9"),
    ],
)
def test_rejects_unsafe_job(monkeypatch, field, value):
    data = responses()
    data[2]["jobs"][0][field] = value
    bind(monkeypatch, data)
    with pytest.raises(ValueError):
        MODULE.prepare_rerun(REPO, 7, 8, HEAD)


def test_fails_on_base_race(monkeypatch):
    data = responses()
    data[-1]["base"]["sha"] = "c" * 40
    bind(monkeypatch, data)
    with pytest.raises(ValueError, match="base changed"):
        MODULE.prepare_rerun(REPO, 7, 8, HEAD)


@pytest.mark.parametrize(
    "repo,pr,run,head", [("../bad", 7, 8, HEAD), (REPO, 0, 8, HEAD), (REPO, 7, 8, "short")]
)
def test_rejects_bad_arguments(repo, pr, run, head):
    with pytest.raises(ValueError):
        MODULE.prepare_rerun(repo, pr, run, head)


@pytest.mark.parametrize("execute", [False, True])
def test_cli_dry_run_and_explicit_execution(monkeypatch, capsys, execute):
    queue = iter(responses() * (2 if execute else 1))
    writes = []

    def fake_run(args, **kwargs):
        if "POST" in args:
            writes.append(args)
            return SimpleNamespace(returncode=0)
        return SimpleNamespace(stdout=json.dumps(next(queue)))

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(
        sys,
        "argv",
        [str(PATH), "--pr", "7", "--run", "8", "--head", HEAD] + (["--execute"] if execute else []),
    )
    runpy.run_path(str(PATH), run_name="__main__")
    assert json.loads(capsys.readouterr().out)["rerun_requested"] is execute
    assert len(writes) == int(execute)
    if execute:
        assert writes[0] == ["gh", "api", "--method", "POST", f"repos/{REPO}/actions/jobs/99/rerun"]


def test_cli_rechecks_target_before_write(monkeypatch):
    plans = iter([{"endpoint": "first"}, {"endpoint": "second"}])
    monkeypatch.setattr(MODULE, "prepare_rerun", lambda *_: next(plans))
    monkeypatch.setattr(
        sys, "argv", [str(PATH), "--pr", "7", "--run", "8", "--head", HEAD, "--execute"]
    )
    with pytest.raises(ValueError, match="target changed"):
        MODULE.main()


@pytest.mark.parametrize("fault", ["stale_head", "api_failure", "post_failure"])
def test_execution_faults_never_report_success(monkeypatch, capsys, fault):
    data = responses() + responses()
    if fault == "stale_head":
        data[5]["head"]["sha"] = "c" * 40
    queue = iter(data)
    writes = []

    def fake_run(args, **kwargs):
        if fault == "api_failure":
            raise subprocess.CalledProcessError(1, args)
        if "POST" in args:
            writes.append(args)
            raise subprocess.CalledProcessError(1, args)
        return SimpleNamespace(stdout=json.dumps(next(queue)))

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(
        sys, "argv", [str(PATH), "--pr", "7", "--run", "8", "--head", HEAD, "--execute"]
    )
    with pytest.raises((ValueError, subprocess.CalledProcessError)):
        runpy.run_path(str(PATH), run_name="__main__")
    assert not capsys.readouterr().out
    assert len(writes) == (1 if fault == "post_failure" else 0)

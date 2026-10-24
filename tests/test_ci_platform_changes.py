"""Affected platforms must run their own tests; skips are not test evidence."""

from pathlib import Path

import pytest
import yaml

from scripts import ci_platform_changes as routing

ALL = {"web", "backend", "android", "infra", "database"}
ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "paths, expected",
    [
        (["android/app/src/main/Main.kt"], {"android"}),
        (["cloud/cloudru/client.py"], {"infra"}),
        (["requirements-deploy.txt"], {"infra"}),
        (["config/alice/alice-dev-delete-ids.txt"], {"infra"}),
        (["db.py"], {"backend", "database"}),
        (["db_backend.py"], {"backend", "database"}),
        (["tests/test_user_identity.py"], {"backend"}),
        (["static/app.js"], {"web", "backend", "android"}),
        (["templates/index.html"], {"web", "backend", "android"}),
        (["tests/test_frontend_smoke.js"], {"web", "backend", "android"}),
        (["docs/README.md"], set()),
        (["android/README.md"], set()),
        (["README.md", "docs/operations.md"], set()),
        (["tests/test_ci_platform_changes.py"], {"infra"}),
        (["scripts/ci_platform_changes.py"], {"infra"}),
        ([".github/workflows/ci.yml"], ALL),
        (["requirements.txt"], ALL),
        # Manifests that Dependabot PRs touch (docs/development/dependency-updates.md).
        (["android/build.gradle.kts"], {"android"}),
        ([".github/workflows/security.yml"], {"infra"}),
        (["requirements-dev.txt"], ALL),
        (["new-platform/entry.wasm"], ALL),
        ([], ALL),
    ],
)
def test_classification_covers_real_platform_inputs(paths, expected):
    plan = routing.classify(paths)
    assert set(plan) == ALL
    assert {name for name, value in plan.items() if value} == expected


def test_routing_unions_all_changed_paths():
    plan = routing.classify(["android/app/build.gradle.kts", "cloud/cloudru/client.py"])
    assert {name for name, value in plan.items() if value} == {"android", "infra"}


def results(**overrides):
    value = dict.fromkeys(["backend", "infra"], "skipped")
    value.update({"changes": "success"})
    value.update(overrides)
    return value


def test_selected_platform_cannot_be_skipped():
    plan = routing.classify(["cloud/cloudru/client.py"])
    with pytest.raises(ValueError, match="infra"):
        routing.validate_results(plan, results())


@pytest.mark.parametrize("state", ["failure", "cancelled", "skipped", "", "queued"])
def test_selected_platform_requires_success(state):
    plan = routing.classify(["cloud/cloudru/client.py"])
    with pytest.raises(ValueError, match="infra"):
        routing.validate_results(plan, results(infra=state))


def test_successful_selected_platform_accepts_unselected_skips():
    plan = routing.classify(["cloud/cloudru/client.py"])
    routing.validate_results(plan, results(infra="success"))


@pytest.mark.parametrize("job", ["changes"])
def test_router_failure_cannot_pass(job):
    plan = routing.classify(["docs/README.md"])
    with pytest.raises(ValueError, match=job):
        routing.validate_results(plan, results(**{job: "failure"}))


def test_missing_job_result_fails_closed():
    plan = routing.classify(["docs/README.md"])
    incomplete = results()
    del incomplete["infra"]
    with pytest.raises(ValueError, match="infra"):
        routing.validate_results(plan, incomplete)


def test_malformed_plan_fails_closed():
    plan = routing.classify(["docs/README.md"])
    plan["infra"] = "false"
    with pytest.raises(ValueError, match="plan"):
        routing.validate_results(plan, results())


def test_removed_code_rules_is_not_a_workflow_job_or_aggregate_dependency():
    jobs = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())["jobs"]
    assert "code-rules" not in jobs
    assert set(jobs["required"]["needs"]) == {"changes", "backend", "infra"}


def test_workflow_has_real_independent_infra_and_backend_jobs():
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    jobs = workflow["jobs"]
    for job in ("backend", "infra"):
        assert jobs[job]["needs"] == "changes"
    assert "infra" in jobs["required"]["needs"]
    infra = "\n".join(step.get("run", "") for step in jobs["infra"]["steps"])
    assert "pytest" in infra and "tests/test_cloudru" in infra
    backend = "\n".join(step.get("run", "") for step in jobs["backend"]["steps"])
    assert "npm test --prefix deploy/chrome-worker" not in backend
    required = "\n".join(step.get("run", "") for step in jobs["required"]["steps"])
    assert "--verify" in required


@pytest.mark.parametrize("path", ["/outside.py", "../outside.py"])
def test_invalid_paths_are_not_silently_skipped(path):
    assert all(routing.classify([path]).values())


@pytest.mark.parametrize("mode", [[], ["--null"]])
def test_cli_classifies_stdin(monkeypatch, capsys, mode):
    import io
    import json

    monkeypatch.setattr(routing.sys, "argv", ["ci_platform_changes.py", *mode])
    separator = "\0" if mode else "\n"
    monkeypatch.setattr(routing.sys, "stdin", io.StringIO("cloud/cloudru/client.py" + separator))
    assert routing.main() == 0
    assert json.loads(capsys.readouterr().out)["infra"] is True


def test_cli_can_select_android_for_manual_build(monkeypatch, capsys):
    import json

    monkeypatch.setattr(routing.sys, "argv", ["ci_platform_changes.py", "--platform", "android"])
    assert routing.main() == 0
    plan = json.loads(capsys.readouterr().out)
    assert {name for name, enabled in plan.items() if enabled} == {"android"}


def test_cli_all_is_conservative(monkeypatch, capsys):
    import json

    monkeypatch.setattr(routing.sys, "argv", ["ci_platform_changes.py", "--all"])
    assert routing.main() == 0
    assert all(json.loads(capsys.readouterr().out).values())


@pytest.mark.parametrize("infra_result, expected", [("success", 0), ("skipped", 1)])
def test_cli_checks_actual_needed_job_results(monkeypatch, infra_result, expected):
    import json

    plan = routing.classify(["cloud/cloudru/client.py"])
    monkeypatch.setenv("CI_PLATFORM_PLAN", json.dumps({k: str(v).lower() for k, v in plan.items()}))
    monkeypatch.setenv(
        "CI_JOB_RESULTS",
        json.dumps({k: {"result": v} for k, v in results(infra=infra_result).items()}),
    )
    monkeypatch.setattr(routing.sys, "argv", ["ci_platform_changes.py", "--verify"])
    assert routing.main() == expected


@pytest.mark.parametrize("plan, needs", [("{}", "{}"), ("[]", "{}"), ("null", "null")])
def test_bad_environment_cannot_create_green_ci(monkeypatch, plan, needs):
    monkeypatch.setenv("CI_PLATFORM_PLAN", plan)
    monkeypatch.setenv("CI_JOB_RESULTS", needs)
    monkeypatch.setattr(routing.sys, "argv", ["ci_platform_changes.py", "--verify"])
    assert routing.main() == 1


def test_incomplete_web_plan_cannot_hide_dependencies():
    plan = routing.classify(["static/app.js"])
    plan["android"] = False
    with pytest.raises(ValueError, match="web dependency plan"):
        routing.validate_results(plan, results(backend="success", android="success"))

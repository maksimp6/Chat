"""Affected platforms must run their own tests; skips are not test evidence."""

from pathlib import Path

import pytest
import yaml

from scripts import ci_platform_changes as routing


ALL = {"web", "backend", "android", "infra", "database", "mcp"}
ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "paths, expected",
    [
        (["android/app/src/main/Main.kt"], {"android"}),
        (["requirements-deploy.txt"], {"infra"}),
        (["config/alice/alice-dev-delete-ids.txt"], {"infra"}),
        (["deploy/remote-desktop-commander/credential-handoff.mjs"], {"infra", "mcp"}),
        (["deploy/chrome-worker/oauth.mjs"], {"infra", "mcp"}),
        (["deploy/oauth-idp/server.mjs"], {"infra", "mcp"}),
        (["requirements-postgres.txt"], {"backend", "database"}),
        (["db.py"], {"backend", "database"}),
        (["db_backend.py"], {"backend", "database"}),
        (["tests/test_user_identity.py"], {"backend"}),
        (["static/app.js"], {"web", "backend", "android", "database", "mcp"}),
        (["templates/index.html"], {"web", "backend", "android", "database", "mcp"}),
        (["tests/test_frontend_smoke.js"], {"web", "backend", "android", "database", "mcp"}),
        (["docs/README.md"], set()),
        (["README.md", "docs/operations.md"], set()),
        (["tests/test_ci_platform_changes.py"], {"infra"}),
        (["scripts/ci_platform_changes.py"], {"infra"}),
        ([".github/workflows/ci.yml"], ALL),
        (["requirements.txt"], ALL),
        (["new-platform/entry.wasm"], ALL),
        ([], ALL),
    ],
)
def test_classification_covers_real_platform_inputs(paths, expected):
    plan = routing.classify(paths)
    assert set(plan) == ALL
    assert {name for name, value in plan.items() if value} == expected




def results(**overrides):
    value = dict.fromkeys(["backend", "postgres", "android", "infra", "mcp"], "skipped")
    value.update({"changes": "success", "code-rules": "success"})
    value.update(overrides)
    return value




@pytest.mark.parametrize("state", ["failure", "cancelled", "skipped", "", "queued"])
def test_selected_platform_requires_success(state):
    plan = routing.classify(["android/app/src/main/Main.kt"])
    with pytest.raises(ValueError, match="android"):
        routing.validate_results(plan, results(android=state))




@pytest.mark.parametrize("job", ["changes", "code-rules"])
def test_router_or_code_rules_failure_cannot_pass(job):
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


def test_web_dependency_requires_postgres_and_mcp():
    plan = routing.classify(["static/app.js"])
    with pytest.raises(ValueError, match="postgres|mcp"):
        routing.validate_results(plan, results(backend="success", android="success"))


def test_router_tests_are_part_of_always_run_code_rules():
    assert "tests/test_ci_platform_changes.py" in (ROOT / "scripts/check_code_rules.sh").read_text()




@pytest.mark.parametrize("path", ["/outside.py", "../outside.py"])
def test_invalid_paths_are_not_silently_skipped(path):
    assert all(routing.classify([path]).values())


@pytest.mark.parametrize("mode", [[], ["--null"]])


def test_cli_all_is_conservative(monkeypatch, capsys):
    import json

    monkeypatch.setattr(routing.sys, "argv", ["ci_platform_changes.py", "--all"])
    assert routing.main() == 0
    assert all(json.loads(capsys.readouterr().out).values())


@pytest.mark.parametrize("infra_result, expected", [("success", 0), ("skipped", 1)])


@pytest.mark.parametrize("plan, needs", [("{}", "{}"), ("[]", "{}"), ("null", "null")])
def test_bad_environment_cannot_create_green_ci(monkeypatch, plan, needs):
    monkeypatch.setenv("CI_PLATFORM_PLAN", plan)
    monkeypatch.setenv("CI_JOB_RESULTS", needs)
    monkeypatch.setattr(routing.sys, "argv", ["ci_platform_changes.py", "--verify"])
    assert routing.main() == 1


def test_incomplete_web_plan_cannot_hide_dependencies():
    plan = routing.classify(["static/app.js"])
    plan["mcp"] = False
    with pytest.raises(ValueError, match="web dependency plan"):
        routing.validate_results(
            plan, results(backend="success", android="success", postgres="success")
        )

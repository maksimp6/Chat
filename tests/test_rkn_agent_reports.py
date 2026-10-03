import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tomllib

import pytest
import yaml

from agent_skills.registry import SkillRegistry
from scripts.rkn_agent_reports import ROLES, main, validate_reports

ROOT = Path(__file__).resolve().parents[1]
HEAD = "a" * 40
NOW = datetime(2026, 10, 3, 12, tzinfo=timezone.utc)


def reports():
    return [
        {
            "role": role,
            "head_sha": HEAD,
            "status": "pass",
            "summary": "Synthetic fixture only; not compliance evidence.",
            "checked_at": NOW.isoformat(),
            "evidence": [
                {
                    "kind": "test-artifact",
                    "source": "synthetic/result.json",
                    "checked_at": NOW.isoformat(),
                }
            ],
        }
        for role in ROLES
    ]


def test_complete_handoff_never_authorizes_release():
    result = validate_reports(reports(), HEAD, now=NOW)
    assert result["handoff_ready"] is True
    assert result["release_authorized"] is False
    assert result["errors"] == []


@pytest.mark.parametrize("status", ["unknown", "fail", "PASS", "", None, []])
def test_no_optimistic_status_fallback(status):
    data = reports()
    data[0]["status"] = status
    assert not validate_reports(data, HEAD, now=NOW)["handoff_ready"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("head_sha", "b" * 40),
        ("role", "made-up"),
        ("role", []),
        ("summary", "  "),
        ("evidence", []),
        ("evidence", {}),
        ("evidence", [None]),
        ("evidence", [{}]),
        ("checked_at", "2026-10-03T12:00:00"),
        ("checked_at", "2026-10-03T12:00:00+03:00"),
        ("checked_at", "not-a-time"),
    ],
)
def test_invalid_or_mismatched_report_blocks(field, value):
    data = reports()
    data[0][field] = value
    assert not validate_reports(data, HEAD, now=NOW)["handoff_ready"]


@pytest.mark.parametrize("hours", [-1, 25])
def test_future_and_stale_report_or_evidence_blocks(hours):
    for target in ("report", "evidence"):
        data = reports()
        item = data[0] if target == "report" else data[0]["evidence"][0]
        item["checked_at"] = (NOW - timedelta(hours=hours)).isoformat()
        assert not validate_reports(data, HEAD, now=NOW)["handoff_ready"]


def test_missing_duplicate_and_non_object_reports_block():
    for data in ([], reports()[:-1], reports() + [reports()[0]], reports() + [None]):
        assert not validate_reports(data, HEAD, now=NOW)["handoff_ready"]


def test_invalid_expected_head_and_age_block():
    assert not validate_reports(reports(), "main", now=NOW)["handoff_ready"]
    assert not validate_reports(reports(), HEAD, now=NOW, max_age=timedelta(0))["handoff_ready"]


def test_validation_does_not_rewrite_input():
    data = reports()
    before = copy.deepcopy(data)
    validate_reports(data, HEAD, now=NOW)
    assert data == before


def test_cli_malformed_input_fails_without_echoing_content(tmp_path, capsys):
    path = tmp_path / "report.json"
    path.write_text("private-fixture-value invalid JSON")
    assert main(["--head", HEAD, str(path)]) == 1
    output = capsys.readouterr().out
    assert "private-fixture-value" not in output
    assert json.loads(output)["release_authorized"] is False


def test_cli_success_and_missing_role(tmp_path, capsys):
    data = reports()
    paths = []
    timestamp = datetime.now(timezone.utc).isoformat()
    for report in data:
        report["checked_at"] = timestamp
        report["evidence"][0]["checked_at"] = timestamp
        path = tmp_path / (report["role"] + ".json")
        path.write_text(json.dumps(report))
        paths.append(str(path))
    assert main(["--head", HEAD, *paths]) == 0
    assert json.loads(capsys.readouterr().out)["release_authorized"] is False
    assert main(["--head", HEAD, *paths[:-1]]) == 1
    assert not json.loads(capsys.readouterr().out)["handoff_ready"]


def test_profiles_load_with_matching_roles_and_bounded_tools():
    skill = ROOT / ".agents/skills/rkn-compliance/SKILL.md"
    assert skill.is_file()
    assert "rkn-compliance" in {s["name"] for s in SkillRegistry().validate_all()}
    for role in ROLES:
        profile = tomllib.loads((ROOT / f".codex/agents/{role}.toml").read_text())
        github_text = (ROOT / f".github/agents/{role}.agent.md").read_text()
        github = yaml.safe_load(github_text.split("---", 2)[1])
        assert profile["name"] == role
        assert profile["model"] == github["model"] == "gpt-5.5"
        assert profile["model_reasoning_effort"] == "low"
        assert github["disable-model-invocation"] is True
        assert github["user-invocable"] is True
        assert set(github["tools"]) <= {"read", "search", "execute"}
        assert str(skill.relative_to(ROOT)) in profile["developer_instructions"]
        assert str(skill.relative_to(ROOT)) in github_text
        if role in {"rkn-law", "data-mapping", "policy-diff"}:
            assert profile["sandbox_mode"] == "read-only"
            assert "execute" not in github["tools"]
        else:
            assert profile["sandbox_mode"] == "workspace-write"

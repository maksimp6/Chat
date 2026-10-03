import json
from pathlib import Path

from scripts.rkn_compliance_inspector import inspect, link


def _write(root: Path, path: str, content: str) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def test_inspector_is_fail_closed_for_unproven_runtime_controls(tmp_path):
    _write(tmp_path, "requirements.txt", "flask\n")
    actual = inspect(tmp_path)

    assert actual["controls"]["tls_public_endpoint"]["status"] == "unknown"
    assert actual["controls"]["log_retention"]["status"] == "unknown"
    assert actual["controls"]["password_storage"]["status"] == "unknown"


def test_backup_requires_script_and_verification_evidence(tmp_path):
    _write(tmp_path, "scripts/pg_backup.sh", "#!/bin/sh\n")
    _write(tmp_path, ".github/workflows/ci.yml", "scripts/pg_backup.sh verify dump\n")

    actual = inspect(tmp_path)

    assert actual["controls"]["backup_restore"]["status"] == "pass"
    assert len(actual["controls"]["backup_restore"]["evidence"]) == 2


def test_linker_rejects_unknown_required_control():
    actual = {"controls": {"tls_public_endpoint": {"status": "unknown"}}, "external_services": []}
    declared = {"required_controls": ["tls_public_endpoint"], "allowed_external_services": []}

    assert link(actual, declared) == ["required control 'tls_public_endpoint' is 'unknown'"]


def test_linker_rejects_undeclared_external_service():
    actual = {"controls": {}, "external_services": ["openai"]}
    declared = {"required_controls": [], "allowed_external_services": ["yandex"]}

    assert link(actual, declared) == ["undeclared external service: openai"]


def test_declared_intent_keeps_legal_actions_human_confirmed():
    declared = json.loads(
        (Path(__file__).parents[1] / "compliance/rkn/declared-intent.json").read_text()
    )

    assert "submit_rkn_notification" in declared["human_approval_required"]
    assert "change_rkn_notification" in declared["human_approval_required"]

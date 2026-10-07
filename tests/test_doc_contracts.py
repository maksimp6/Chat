from __future__ import annotations

from pathlib import Path

from scripts.check_doc_contracts import validate_contracts


def _entry(**overrides):
    value = {
        "id": "runtime.sessions",
        "status": "current",
        "doc": "docs/runtime_serverless.md",
        "implementation": ["runtime_api.py"],
        "tests": ["tests/test_runtime_api.py"],
    }
    value.update(overrides)
    return value


def test_current_contract_requires_existing_doc_implementation_and_test(tmp_path):
    for path in ("docs/runtime_serverless.md", "runtime_api.py", "tests/test_runtime_api.py"):
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("", encoding="utf-8")
    assert validate_contracts(tmp_path, [_entry()]) == []


def test_current_contract_without_test_is_rejected(tmp_path):
    doc = tmp_path / "docs/runtime_serverless.md"
    impl = tmp_path / "runtime_api.py"
    doc.parent.mkdir(parents=True)
    doc.write_text("", encoding="utf-8")
    impl.write_text("", encoding="utf-8")
    errors = validate_contracts(tmp_path, [_entry(tests=[])])
    assert errors == ["runtime.sessions: DOC_WITHOUT_TEST"]


def test_missing_doc_is_rejected(tmp_path):
    errors = validate_contracts(tmp_path, [_entry(status="current")])
    assert "runtime.sessions: missing doc: docs/runtime_serverless.md" in errors


def test_planned_contract_does_not_require_runtime_proof(tmp_path):
    doc = tmp_path / "docs/future.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("", encoding="utf-8")
    entry = _entry(
        id="future.contract",
        status="planned",
        doc="docs/future.md",
        implementation=[],
        tests=[],
    )
    assert validate_contracts(tmp_path, [entry]) == []


def test_duplicate_contract_id_is_rejected(tmp_path):
    errors = validate_contracts(tmp_path, [_entry(), _entry()])
    assert "runtime.sessions: duplicate contract id" in errors

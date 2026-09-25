import os
import tempfile

import db
from flask import Flask

from government import (
    approve_case,
    collect_data,
    create_case,
    government_bp,
    init_government_tables,
    prepare_documents,
    submit_case,
    validate_data,
)
from tool_registry import ToolRegistry


def _setup(tmp):
    db.DB_PATH = os.path.join(tmp, "government.db")
    db.init_db()
    init_government_tables()
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(government_bp)
    return app


def _complete_case():
    case = create_case("IP_REGISTRATION", "user-1")
    collect_data(case["case_id"], {
        "full_name": "Test User",
        "birth_date": "1990-01-01",
        "citizenship": "RU",
        "passport": "TEST-PASSPORT",
        "okved": ["62.01"],
    })
    assert validate_data(case["case_id"])["valid"] is True
    prepare_documents(case["case_id"])
    return case["case_id"]


def test_ip_case_lifecycle_requires_explicit_approval():
    old = db.DB_PATH
    with tempfile.TemporaryDirectory() as tmp:
        try:
            _setup(tmp)
            case_id = _complete_case()
            try:
                submit_case(case_id)
            except PermissionError:
                pass
            else:
                raise AssertionError("submission must be blocked before approval")

            approved = approve_case(case_id)
            assert approved["user_approved"] is True
            submitted = submit_case(case_id)
            assert submitted["status"] == "SUBMITTED"
        finally:
            db.DB_PATH = old


def test_government_http_case_and_approval_gate():
    old = db.DB_PATH
    with tempfile.TemporaryDirectory() as tmp:
        try:
            client = _setup(tmp).test_client()
            created = client.post("/api/government/cases", json={"case_type": "IP_REGISTRATION", "user_id": "user-1"})
            assert created.status_code == 201
            case_id = created.get_json()["case"]["case_id"]

            response = client.post(f"/api/government/cases/{case_id}/submit")
            assert response.status_code == 409
        finally:
            db.DB_PATH = old


def test_government_tools_are_registered_for_universal_transports():
    registry = ToolRegistry()
    meta = registry.get_tool_meta("gov.submit")
    assert meta is not None
    assert meta["requires_approval"] is True
    assert "mcp" in meta["supported_transports"]
    assert "gov.status" in registry.get_available_categories()["government"]


def test_validation_returns_field_names_without_pii_values():
    old = db.DB_PATH
    with tempfile.TemporaryDirectory() as tmp:
        try:
            _setup(tmp)
            case = create_case("IP_REGISTRATION")
            result = validate_data(case["case_id"])
            assert result["valid"] is False
            assert "passport" in {item["field"] for item in result["errors"]}
            assert "TEST-PASSPORT" not in str(result)
        finally:
            db.DB_PATH = old

import copy
import json
from pathlib import Path
import subprocess
import sys

from scripts.validate_integration_catalog import (
    ALLOWED_STATUSES,
    CATALOG_PATH,
    DOC_PATH,
    SCHEMA_PATH,
    load_json,
    render_markdown,
    stale_integrations,
    validate_catalog,
    validate_schema_enum,
)


def test_catalog_and_schema_are_valid_and_seeded():
    data = load_json(CATALOG_PATH)
    schema = load_json(SCHEMA_PATH)

    assert validate_catalog(data) == []
    assert validate_schema_enum(schema) == []
    assert {item["id"] for item in data["integrations"]} == {
        "mcp",
        "browser",
        "sber_business",
        "gigachat",
    }
    assert (
        set(schema["properties"]["integrations"]["items"]["properties"]["status"]["enum"])
        == ALLOWED_STATUSES
    )


def test_human_view_is_exactly_generated_from_machine_source():
    data = load_json(CATALOG_PATH)
    assert DOC_PATH.read_text(encoding="utf-8") == render_markdown(data)


def test_stale_reporting_is_deterministic_from_catalog_as_of_date():
    data = load_json(CATALOG_PATH)
    assert stale_integrations(data) == ["sber_business", "gigachat"]


def test_duplicate_id_is_rejected():
    data = load_json(CATALOG_PATH)
    broken = copy.deepcopy(data)
    broken["integrations"].append(copy.deepcopy(broken["integrations"][0]))

    errors = validate_catalog(broken)

    assert any("duplicate integration id: mcp" in error for error in errors)


def test_supported_requires_provider_implementation_and_e2e():
    data = load_json(CATALOG_PATH)
    broken = copy.deepcopy(data)
    record = broken["integrations"][2]
    record["status"] = "supported"
    record["last_verified"] = broken["as_of"]

    errors = validate_catalog(broken)

    assert any(
        "supported requires provider + implementation + E2E verification" in error
        for error in errors
    )


def test_e2e_cannot_exist_without_implementation():
    data = load_json(CATALOG_PATH)
    broken = copy.deepcopy(data)
    record = broken["integrations"][2]
    record["e2e_verified"] = True

    errors = validate_catalog(broken)

    assert any("e2e_verified requires alice_implemented" in error for error in errors)


def test_catalog_rejects_secret_value_fields():
    data = load_json(CATALOG_PATH)
    broken = copy.deepcopy(data)
    broken["integrations"][0]["token"] = "must-never-live-here"

    errors = validate_catalog(broken)

    assert any("forbidden secret-value keys" in error for error in errors)


def test_validator_cli_checks_generated_view():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "scripts/validate_integration_catalog.py"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "integration catalog valid: 4 records" in result.stdout

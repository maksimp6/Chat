"""Contract-shape checks; runtime validation remains a separate integration task."""

import json
from pathlib import Path

SCHEMA = Path(__file__).resolve().parents[1] / "contracts/memory/v1.schema.json"


def test_memory_protocol_has_only_get_and_set_requests():
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    variants = schema["$defs"]["request"]["oneOf"]
    assert {item["properties"]["operation"]["const"] for item in variants} == {"get", "set"}
    assert all(item["properties"]["version"]["const"] == 1 for item in variants)
    assert all(item["additionalProperties"] is False for item in variants)


def test_set_requires_value_and_get_does_not_accept_it():
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    get, set_request = schema["$defs"]["request"]["oneOf"]
    assert "value" not in get["properties"]
    assert "value" in set_request["required"]
    assert "id" in get["required"] and "id" in set_request["required"]


def test_get_distinguishes_missing_from_json_null():
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    missing, found = schema["$defs"]["response"]["oneOf"][:2]
    assert missing["properties"]["found"]["const"] is False
    assert "value" not in missing["properties"]
    assert found["properties"]["found"]["const"] is True
    assert "value" in found["required"]


def test_errors_have_bounded_codes_and_no_raw_exception_field():
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    failure = schema["$defs"]["response"]["oneOf"][-1]
    codes = failure["properties"]["error"]["properties"]["code"]["enum"]
    assert "WRITE_FAILED" in codes
    assert "traceback" not in failure["properties"]["error"]["properties"]

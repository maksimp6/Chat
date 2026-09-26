import pytest

from api_contracts import ContractViolation, validate_api_contract


def test_contract_rejects_unknown_contract_name():
    with pytest.raises(ContractViolation, match="unknown API contract"):
        validate_api_contract("runtime.missing", {})


def test_contract_rejects_extra_fields_and_missing_required_fields():
    with pytest.raises(ContractViolation, match="unexpected fields: extra"):
        validate_api_contract(
            "runtime.invocation.create.request",
            {"conversation_id": "conv-1", "extra": True},
        )

    with pytest.raises(ContractViolation, match="missing required fields: conversation_id"):
        validate_api_contract("runtime.invocation.create.request", {})


def test_contract_rejects_wrong_types_blank_strings_and_bool_as_integer():
    with pytest.raises(ContractViolation, match="expected object"):
        validate_api_contract("runtime.session.create.request", {"metadata": "bad"})

    with pytest.raises(ContractViolation, match="string must not be empty"):
        validate_api_contract(
            "runtime.invocation.create.request",
            {"conversation_id": "   "},
        )

    payload = {
        "id": "session-1",
        "status": "active",
        "metadata": {},
        "created_at": True,
        "updated_at": 1,
        "completed_at": None,
    }
    with pytest.raises(ContractViolation, match="expected integer"):
        validate_api_contract("runtime.session.response", payload)


def test_contract_rejects_unknown_response_fields():
    payload = {
        "id": "session-1",
        "status": "active",
        "metadata": {},
        "created_at": 1,
        "updated_at": 1,
        "completed_at": None,
        "surprise": "silent API drift",
    }
    with pytest.raises(ContractViolation, match="unexpected fields: surprise"):
        validate_api_contract("runtime.session.response", payload)


def test_profile_contract_is_recursive_and_strict():
    profile = {
        "id": "developer",
        "name": "Developer",
        "type": "developer",
        "model": "",
        "system_prompt": "",
        "tools": ["filesystem"],
        "environment": {},
        "files": [],
        "state": {},
        "virtual_server": {
            "runtime": "python",
            "cwd": ".",
            "env": {},
            "memory_mb": 256,
            "cpu_seconds": 30,
            "command_timeout_seconds": 30,
            "idle_timeout_seconds": 300,
            "max_output_bytes": 1000000,
        },
        "template": True,
    }
    validate_api_contract("runtime.profile.response", profile)

    profile["virtual_server"]["memory_mb"] = "256"
    with pytest.raises(ContractViolation, match="expected integer"):
        validate_api_contract("runtime.profile.response", profile)


def test_trace_contract_rejects_top_level_drift():
    trace = {
        "trace_id": "trace-1",
        "context": {
            "invocation_id": "inv-1",
            "session_id": "sess-1",
            "conversation_id": "conv-1",
            "trace_id": "trace-1",
        },
        "responses": [],
        "tool_calls": [],
        "events": [],
        "errors": [],
        "mystery": {},
    }
    with pytest.raises(ContractViolation, match="unexpected fields: mystery"):
        validate_api_contract("runtime.invocation.trace.response", trace)

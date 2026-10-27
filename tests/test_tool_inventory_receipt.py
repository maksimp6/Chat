"""Receipt inventories survive native trace sanitization without tool bodies.

Run only in a GitHub runner job; no provider or device operations are performed.
"""

import json

import pytest

from observability.trace_manager import ExecutionTrace
from yandex_client_modules.request_receipt import build_request_manifest


def flatten(tools):
    return [name for batch in tools["function_name_batches"] for name in batch]


def test_all_93_offered_function_names_survive_repeated_trace_sanitization():
    names = ["tool_" + str(index) for index in range(93)]
    payload = {"tools": [{"type": "web_search"}] + [
        {"type": "function", "name": name,
         "description": "PRIVATE_DESCRIPTION", "parameters": {
             "type": "object", "properties": {"secret_path": {"const": "PRIVATE_ARGUMENT"}},
         }} for name in names
    ]}
    manifest = build_request_manifest(payload)
    trace = ExecutionTrace(trace_id="inventory-regression")
    trace.add_api_request(payload)
    trace.add_event("api_request_manifest", {"manifest": manifest})
    # Model the saved invocation plus the final HTTP sanitization pass too.
    saved = ExecutionTrace._sanitize_trace_value(trace.finalize())
    public = ExecutionTrace._sanitize_trace_value(saved)
    captured = next(event["payload"]["manifest"]["tools"] for event in public["events"]
                    if event["type"] == "api_request_manifest")
    assert captured["types"]["function"] == captured["function_name_count"] == 93
    assert captured["types"]["web_search"] == 1
    assert captured["function_names_complete"] is True
    assert flatten(captured) == names
    assert [len(batch) for batch in captured["function_name_batches"]] == [40, 40, 13]
    assert "PRIVATE_DESCRIPTION" not in json.dumps(captured)
    assert "PRIVATE_ARGUMENT" not in json.dumps(captured)


def test_nested_function_names_remain_exact_and_ordered():
    names = ["gov.collect_data", "file_stat", "a" * 128, "read-file"]
    manifest = build_request_manifest({"tools": [
        {"type": "function", "function": {"name": name}} for name in names
    ]})["tools"]
    assert manifest["function_names_complete"] is True
    assert manifest["function_name_count"] == len(names)
    assert flatten(manifest) == names


@pytest.mark.parametrize("malformed", [
    {"type": "function"},
    {"type": "function", "name": "bad name"},
    {"type": "function", "name": "a" * 129},
    {"type": "function", "name": "sk-synthetic-secret"},
    {"type": "function", "name": "ghp_synthetic_secret"},
    {"type": "function", "name": "BEGIN_PRIVATE_KEY"},
    {"type": "function", "name": 123},
    {"type": "function", "function": {"name": []}},
])
def test_unsafe_or_missing_function_name_is_not_truncated_or_marked_complete(malformed):
    manifest = build_request_manifest({"tools": [
        {"type": "function", "name": "read_file"}, malformed,
    ]})["tools"]
    assert manifest["function_name_count"] == manifest["types"]["function"] == 2
    assert manifest["function_names_complete"] is False
    assert flatten(manifest) == ["read_file"]


@pytest.mark.parametrize("malformed", [None, "invalid", {"name": "unknown_type"}])
def test_malformed_tool_entry_cannot_produce_complete_name_inventory(malformed):
    manifest = build_request_manifest({"tools": [
        {"type": "function", "name": "read_file"}, malformed,
    ]})["tools"]
    assert manifest["function_names_complete"] is False
    assert manifest["function_name_count"] == 1
    assert manifest["types"]["other"] == 1


def test_capture_budget_never_alters_actual_function_count_or_provider_payload():
    names = ["function_" + str(index) for index in range(2001)]
    payload = {"tools": [{"type": "function", "name": name} for name in names]}
    manifest = build_request_manifest(payload)["tools"]
    assert manifest["types"]["function"] == manifest["function_name_count"] == 2001
    assert manifest["function_names_complete"] is False
    assert len(manifest["function_name_batches"]) == 50
    assert all(len(batch) == 40 for batch in manifest["function_name_batches"])
    assert flatten(manifest) == names[:2000]
    assert [tool["name"] for tool in payload["tools"]] == names

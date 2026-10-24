"""Real snapshot conversion, payload checks, and corruption/negative controls."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from jsonschema import Draft7Validator

from api.contracts import ContractViolation
from api.yandex_contracts import (
    CATALOG,
    FORMATS,
    IncompleteYandexContract,
    get_yandex_contract,
    schema_gaps,
    validate_yandex_contract,
)
from scripts.build_yandex_contracts import (
    MIRROR,
    ROOT,
    SOURCE,
    build,
    compile_document,
    field_schema,
)

TOKENIZER = "yandex.text-generation.api-ref.Tokenizer.tokenize"


@pytest.fixture(scope="module")
def catalog():
    return json.loads(CATALOG.read_text())


def test_complete_catalog_matches_manifest_and_all_schemas_are_valid(catalog):
    manifest_data = (ROOT / MIRROR / "manifest.json").read_bytes()
    assert hashlib.sha256(manifest_data).hexdigest() == catalog["manifest_sha256"]
    manifest = json.loads(manifest_data)
    expected = {
        str(MIRROR / e["path"]): e["sha256"]
        for e in manifest["files"]
        if e["path"].startswith("source/ru/ai-studio/") and e["path"].endswith(".md")
    }
    assert catalog["coverage"] == {
        "api_documents": 267,
        "excluded_documents": 296,
        "by_transport": {"http": 107, "grpc": 104, "realtime-event": 56},
    }
    paths = {entry["source"]["path"] for entry in catalog["contracts"]}
    paths.update(entry["path"] for entry in catalog["excluded"])
    assert len(paths) == 563
    assert paths == expected.keys()
    for entry in catalog["excluded"]:
        assert entry["sha256"] == expected[entry["path"]]
    for entry in catalog["contracts"]:
        Draft7Validator.check_schema(entry["schema"])
        assert entry["source"]["sha256"] == expected[entry["source"]["path"]]
        for group in ("request", "responses"):
            for ref in entry[group].values():
                # Every binding resolves, even if its type graph is incomplete.
                assert ref.removeprefix("#/definitions/") in entry["schema"]["definitions"]


def test_tokenizer_request_and_nested_response_are_executable():
    payload = {"modelUri": "gpt://example/model", "text": "hello"}
    assert validate_yandex_contract(TOKENIZER, payload) is payload
    response = {"tokens": [{"id": "42", "text": "hello", "special": False}], "modelVersion": "1"}
    assert (
        validate_yandex_contract(TOKENIZER, response, direction="responses", part="200") is response
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"text": 42},
        {"text": None},
        {"text": "hello", "api_key": "secret-marker"},
        [],
        {"text": float("nan")},
        {"text": float("inf")},
        {1: "not-a-json-key"},
    ],
)
def test_bad_request_is_rejected_without_value_disclosure(payload):
    with pytest.raises(ContractViolation) as exc:
        validate_yandex_contract(TOKENIZER, payload)
    assert "secret-marker" not in str(exc.value)


@pytest.mark.parametrize(
    "token",
    [
        {"id": 42},
        {"special": 1},
        {"special": False, "extra": True},
    ],
)
def test_nested_response_drift_is_rejected(token):
    with pytest.raises(ContractViolation):
        validate_yandex_contract(TOKENIZER, {"tokens": [token]}, direction="responses", part="200")


def test_missing_status_and_unknown_responses_type_cannot_pass():
    with pytest.raises(IncompleteYandexContract):
        validate_yandex_contract(TOKENIZER, {}, direction="responses", part="401")
    with pytest.raises(IncompleteYandexContract):
        validate_yandex_contract("yandex.responses.createResponse", {"model": "example"})
    with pytest.raises(ContractViolation):
        validate_yandex_contract("invented", {})


def test_required_enum_and_direction_of_real_realtime_event():
    name = "yandex.clientEvents.realtimeInputAudioBufferCommit"
    contract = get_yandex_contract(name)
    assert contract["transport"]["direction"] == "client-to-server"
    assert validate_yandex_contract(name, {"type": "input_audio_buffer.commit"})
    for payload in ({}, {"type": "wrong"}, {"type": "input_audio_buffer.commit", "extra": 1}):
        with pytest.raises(ContractViolation):
            validate_yandex_contract(name, payload)
    name = "yandex.serverEvents.realtimeServerResponseOutputTextDelta"
    assert get_yandex_contract(name)["transport"]["direction"] == "server-to-client"
    with pytest.raises(IncompleteYandexContract, match="unsupported"):
        validate_yandex_contract(name, {})


def test_grpc_streaming_and_snapshot_immutability():
    contract = get_yandex_contract("yandex.runs.api-ref.grpc.Run.attach")
    assert contract["transport"]["request_stream"] is True
    assert contract["transport"]["response_stream"] is True
    contract["transport"]["request_stream"] = False
    assert get_yandex_contract(contract["name"])["transport"]["request_stream"] is True
    with pytest.raises(IncompleteYandexContract):
        validate_yandex_contract("yandex.files.createFile", {}, part="body")
    assert "128 MB" in get_yandex_contract("yandex.files.createFile")["documentation"]


@pytest.mark.parametrize(
    "definition",
    [
        {"type": "object", "properties": {"optional": {"x-unresolved": "unknown"}}},
        {"anyOf": [{"type": "string"}, {"x-unresolved": "unknown"}]},
        {"$ref": "https://example.invalid/schema"},
        {"$ref": "#/definitions/missing"},
        {"type": "number", "x-prose-constraints": "must be positive"},
    ],
)
def test_unknown_optional_union_external_and_prose_constraints_block(definition):
    assert schema_gaps({"definitions": {"test": definition}}, "#/definitions/test")


def test_recursive_references_are_bounded():
    document = {
        "definitions": {
            "node": {"type": "object", "properties": {"child": {"$ref": "#/definitions/node"}}}
        }
    }
    assert schema_gaps(document, "#/definitions/node") == []


def test_negative_control_source_corruption_lfs_pointer_and_missing_file(tmp_path, monkeypatch):
    relative = Path("text-generation/api-ref/Tokenizer/tokenize.md")
    target = tmp_path / SOURCE / relative
    target.parent.mkdir(parents=True)
    # A source fixture allows the corruption check to run in CI with LFS pointers.
    data = b"# Tokenizer\n\n## HTTP request\n\nPOST https://example.invalid/tokenize\n\n## Body parameters\n\n#|\n||Field | Description ||\n|| text | **string** ||\n|#\n"
    target.write_bytes(data)
    manifest_path = tmp_path / MIRROR / "manifest.json"
    manifest = json.loads((ROOT / MIRROR / "manifest.json").read_text())
    manifest["files"] = [
        e for e in manifest["files"] if e["path"] == "source/ru/ai-studio/" + relative.as_posix()
    ]
    manifest["files"][0].update(sha256=hashlib.sha256(data).hexdigest(), bytes=len(data))
    manifest_path.write_text(json.dumps(manifest))
    assert len(build(tmp_path)["contracts"]) == 1
    from scripts import build_yandex_contracts as builder

    monkeypatch.setattr(builder, "ROOT", tmp_path)
    monkeypatch.setattr("sys.argv", ["build_yandex_contracts.py"])
    assert builder.main() == 0
    monkeypatch.setattr("sys.argv", ["build_yandex_contracts.py", "--check"])
    assert builder.main() == 0
    (tmp_path / builder.OUTPUT).write_text("{}\n")
    with pytest.raises(SystemExit) as exc:
        builder.main()
    assert exc.value.code == 1
    for corrupt in (data + b"\n", b"version https://git-lfs.github.com/spec/v1\n"):
        target.write_bytes(corrupt)
        with pytest.raises(ValueError, match="modified or still an LFS pointer"):
            build(tmp_path)
    target.unlink()
    with pytest.raises(ValueError, match="inventory"):
        build(tmp_path)


def test_ambiguous_root_union_is_not_flattened_to_a_false_schema():
    contract = get_yandex_contract("yandex.conversations.getConversationItem")
    assert schema_gaps(contract["schema"], contract["responses"]["200"])


def test_converter_rejects_new_unparsed_binding_instead_of_dropping_it():
    source = "# Example\n\n## HTTP request\n\nUNKNOWN /example\n"
    with pytest.raises(ValueError, match="unparsed HTTP"):
        compile_document("new.md", source, "example")


def test_maps_nullable_and_nested_required_types_are_not_flattened():
    spec = field_schema(
        {"name": "labels", "description": "**object** (map<**string**, **string**>)"}, {}
    )
    validator = Draft7Validator(spec)
    assert validator.is_valid({"key": "value"})
    assert not validator.is_valid("not-a-map")
    assert not validator.is_valid({"key": 1})
    nullable = field_schema({"name": "field", "description": "Any of **string** \\| **null**"}, {})
    assert Draft7Validator(nullable).is_valid(None)
    assert Draft7Validator(nullable).is_valid("value")
    assert not Draft7Validator(nullable).is_valid(42)


def test_int64_and_unsupported_formats_never_silently_pass():
    schema = {"type": "string", "format": "int64"}
    validator = Draft7Validator(schema, format_checker=FORMATS)
    assert validator.is_valid(str(2**63 - 1))
    for value in (str(2**63), "wrong", True, "12.5", "0" * 10000, "9" * 10000):
        assert not validator.is_valid(value)
    assert schema_gaps(
        {"definitions": {"field": {"type": "string", "format": "bytes"}}}, "#/definitions/field"
    )

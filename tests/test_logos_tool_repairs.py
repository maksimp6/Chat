"""Exercise provider-advertised tool arguments through the real execution boundary."""

import hashlib
import http.server
import shlex
import sys
import threading
import zipfile
from pathlib import Path

import pytest

from tool_providers import filesystem
from tool_providers.registry import ToolRegistry
from tool_providers.universal_tool_platform import (
    UniversalToolCall,
    UniversalToolExecutor,
    validate_json_schema,
)


@pytest.fixture
def tools(monkeypatch, tmp_path):
    monkeypatch.setattr(filesystem, "BASE_DIR", str(tmp_path))
    backups = tmp_path / "backups"
    backups.mkdir()
    monkeypatch.setattr(filesystem, "BACKUP_DIR", str(backups))
    (tmp_path / "sample.txt").write_text("marker\n")
    (tmp_path / "sample.py").write_text("MARKER = 'marker'\n")
    with zipfile.ZipFile(tmp_path / "payload.zip", "w") as archive:
        archive.writestr("payload.txt", "extracted marker")
    registry = ToolRegistry.__new__(ToolRegistry)
    registry._tools = {}
    registry._categories = {}
    for name, cfg in filesystem.FILESYSTEM_TOOLS.items():
        registry.register("filesystem", name, cfg)
    return registry, UniversalToolExecutor(registry), tmp_path


@pytest.fixture(scope="module")
def http_url():
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"http marker")

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/"
    server.shutdown()
    server.server_close()
    thread.join()


def advertised(registry, name):
    return next(tool["parameters"] for tool in registry.get_definitions() if tool["name"] == name)


def execute(executor, name, arguments, *, approved=False, transport="responses_api"):
    return executor.execute(
        UniversalToolCall(
            tool_name=name, arguments=arguments, transport=transport, approved=approved
        )
    )


@pytest.mark.parametrize(
    "name",
    [
        "read_file",
        "calculate_hash",
        "zip_extract",
        "http_fetch",
        "copy_or_move_file",
        "list_directory",
        "grep_search",
        "run_command",
    ],
)
def test_responses_optional_null_executes_native_defaults(tools, http_url, name):
    registry, executor, root = tools
    arguments = {
        "read_file": {"path": "sample.txt"},
        "calculate_hash": {"path": "sample.txt"},
        "zip_extract": {"archive_path": "payload.zip"},
        "http_fetch": {"url": http_url},
        "copy_or_move_file": {"src": "sample.txt", "dst": "copied.txt"},
        "list_directory": {},
        "grep_search": {"query": "marker"},
        "run_command": {"command": shlex.join([sys.executable, "-c", "print('marker')"])},
    }[name]
    schema = registry.get_universal_definition(name).input_schema
    optional = set(schema["properties"]) - set(schema.get("required", []))
    arguments.update(dict.fromkeys(optional))
    original_arguments = arguments.copy()
    assert not validate_json_schema(arguments, advertised(registry, name))

    result = execute(executor, name, arguments, approved=name == "run_command")

    assert result["success"], result
    assert arguments == original_arguments
    data = result["data"]
    if name == "read_file":
        assert data["content"] == "marker\n"
    elif name == "calculate_hash":
        assert data["digest"] == hashlib.sha256((root / "sample.txt").read_bytes()).hexdigest()
    elif name == "zip_extract":
        assert (root / "payload.txt").read_text() == "extracted marker"
    elif name == "http_fetch":
        assert data["status"] == 200 and data["body"] == "http marker"
    elif name == "copy_or_move_file":
        assert (root / "copied.txt").read_bytes() == (root / "sample.txt").read_bytes()
    elif name == "list_directory":
        assert "sample.txt" in {item["name"] for item in data["items"]}
    elif name == "grep_search":
        assert any(
            item["file"] == "sample.py" and "marker" in item["text"] for item in data["matches"]
        )
    else:
        assert data["returncode"] == 0 and data["stdout"] == "marker"


def test_apply_patch_advertises_executable_contract_and_keeps_approval(tools):
    registry, executor, root = tools
    target = root / "sample.txt"
    target.write_text("before before")
    arguments = {"path": "sample.txt", "search_text": "before", "replace_text": "after"}
    assert not validate_json_schema(arguments, advertised(registry, "apply_patch"))

    blocked = execute(executor, "apply_patch", arguments)
    assert blocked["metadata"]["phase"] == "approval_required"
    assert target.read_text() == "before before"

    result = execute(executor, "apply_patch", arguments, approved=True)
    assert result["success"], result
    assert target.read_text() == "after before"

    arguments["replace_text"] = ""
    result = execute(executor, "apply_patch", arguments, approved=True)
    assert result["success"], result
    assert target.read_text() == "after "


@pytest.mark.parametrize(
    "changes",
    [
        {"path": None},
        {"search_text": None},
        {"replace_text": None},
        {"search_text": ""},
        {"path": 12},
        {"replace_text": 12},
    ],
)
def test_apply_patch_invalid_required_values_do_not_write(tools, changes):
    _, executor, root = tools
    arguments = {"path": "sample.txt", "search_text": "marker", "replace_text": "after"}
    arguments.update(changes)
    result = execute(executor, "apply_patch", arguments, approved=True)
    assert result["success"] is False
    assert result["metadata"]["phase"] == "validation"
    assert (root / "sample.txt").read_text() == "marker\n"


def test_responses_null_normalization_preserves_schema_meaning(tools):
    registry, executor, _ = tools
    schema = {
        "type": "object",
        "properties": {
            "required": {"type": "string"},
            "optional": {"anyOf": [{"type": "string"}, {"type": "integer"}]},
            "nullable": {"type": ["string", "null"]},
            "nullable_any": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            "required_nullable": {"type": ["string", "null"]},
            "nested": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"optional": {"type": "string"}},
                    "additionalProperties": False,
                },
            },
        },
        "required": ["required", "required_nullable"],
        "additionalProperties": False,
    }
    registry.register(
        "test",
        "schema_probe",
        {"parameters": schema, "requires_approval": False, "func": lambda args: args},
    )
    arguments = {
        "required": "kept",
        "optional": None,
        "nullable": None,
        "nullable_any": None,
        "required_nullable": None,
        "nested": [{"optional": None}],
    }
    assert not validate_json_schema(arguments, advertised(registry, "schema_probe"))
    result = execute(executor, "schema_probe", arguments)
    assert result["success"], result
    assert result["data"] == {
        "required": "kept",
        "nullable": None,
        "nullable_any": None,
        "required_nullable": None,
        "nested": [{}],
    }
    assert arguments["nested"] == [{"optional": None}]
    for value in ("text", 7):
        valid = {"required": "kept", "required_nullable": None, "optional": value}
        result = execute(executor, "schema_probe", valid)
        assert result["success"] and result["data"] == valid
    for invalid in [
        {"required": None},
        {"required": "kept", "optional": []},
        {"required": "kept", "unknown": None},
    ]:
        result = execute(executor, "schema_probe", {"required_nullable": None, **invalid})
        assert result["metadata"]["phase"] == "validation"

    result = execute(
        executor,
        "schema_probe",
        {"required": "kept", "required_nullable": None, "optional": None},
        transport="mcp",
    )
    assert result["metadata"]["phase"] == "validation"


def test_filesystem_descriptions_identify_actual_host(tools):
    registry, executor, _ = tools
    for name in ("run_command", "get_system_info"):
        assert "Termux" not in registry.get_universal_definition(name).description
    info = execute(executor, "get_system_info", {})
    assert info["success"]
    assert Path(info["data"]["project_dir"]).resolve() == Path(filesystem.BASE_DIR)

"""Production MCP discovery and tools.execute regression for #652.

Verifies that pytorch tools integrate correctly with the real ToolRegistry
and UniversalToolExecutor pipeline, including MCP transport discovery.
"""

from __future__ import annotations

import pytest


def _make_registry(monkeypatch):
    from tool_registry import ToolRegistry

    monkeypatch.setattr(ToolRegistry, "_load_all", lambda self: None)
    return ToolRegistry()


def test_pytorch_tools_appear_in_mcp_discovery(monkeypatch):
    from pytorch_adapter import PyTorchAdapter, register_pytorch_tools

    registry = _make_registry(monkeypatch)
    register_pytorch_tools(registry, adapter=PyTorchAdapter())

    mcp_defs = registry.get_universal_definitions(transport="mcp")
    names = {d["name"] for d in mcp_defs}
    assert "pytorch_status" in names
    assert "pytorch_smoke" in names

    for name in ("pytorch_status", "pytorch_smoke"):
        defn = next(d for d in mcp_defs if d["name"] == name)
        assert "mcp" in defn["supported_transports"]
        assert defn["inputSchema"].get("additionalProperties") is False
        assert defn["inputSchema"].get("properties") == {}
        assert defn["executor"]["type"] == "local"
        assert defn["requires_approval"] is False


def test_pytorch_status_via_tools_execute_mcp_path(monkeypatch):
    from pytorch_adapter import PyTorchAdapter, register_pytorch_tools
    from universal_tool_platform import UniversalToolCall, UniversalToolExecutor

    registry = _make_registry(monkeypatch)
    register_pytorch_tools(registry, adapter=PyTorchAdapter())
    executor = UniversalToolExecutor(registry)

    call = UniversalToolCall(
        tool_name="pytorch_status",
        arguments={},
        transport="mcp",
        call_id="prod-call-1",
        trace_id="prod-trace-1",
        invocation_id="prod-invocation-1",
        metadata={"runtime_id": "prod-runtime"},
    )
    result = executor.execute(call)
    assert result["success"] is True
    assert result["data"]["state"] == "disabled"
    assert result["metadata"]["runtime_id"] == "prod-runtime"
    assert result["metadata"]["call_id"] == "prod-call-1"


def test_pytorch_unknown_argument_rejected_before_framework(monkeypatch):
    from pytorch_adapter import PyTorchAdapter, register_pytorch_tools
    from universal_tool_platform import UniversalToolCall, UniversalToolExecutor

    attempts = []

    def deny_import():
        attempts.append("torch")
        raise AssertionError("torch must not be loaded")

    registry = _make_registry(monkeypatch)
    register_pytorch_tools(registry, adapter=PyTorchAdapter(enabled=True, importer=deny_import))
    executor = UniversalToolExecutor(registry)

    for tool in ("pytorch_status", "pytorch_smoke"):
        result = executor.execute(
            UniversalToolCall(
                tool_name=tool,
                arguments={"device": "cuda"},
                transport="mcp",
            )
        )
        assert result["success"] is False
        assert result["metadata"]["phase"] == "validation"

    assert attempts == []

"""Executable proposal for #652; intentionally RED until independently accepted.

Public contract proposed here:
* PyTorchAdapter(enabled=False, importer=None), with status() and smoke().
* register_pytorch_tools(registry, adapter=adapter), with two empty-input tools.
* status state: disabled / missing / unavailable / ready.
* smoke: a fixed CPU float32 tensor [1, 2, 3] doubled to [2, 4, 6].

The importer is trusted dependency injection, never a tool argument. Ordinary
contract tests use a tiny fake, not an installed ML framework. The separately
named pytorch_cpu_integration.py must be invoked explicitly by the CPU job.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import subprocess
import sys
import textwrap
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ("pytorch_status", "pytorch_smoke")
SENSITIVE = "synthetic-private-value-652"
INTERNAL_PATH = "/private/adapter-652/native-library.so"


def adapter_module():
    """Turn the intentionally absent feature into a test failure, not collection error."""
    try:
        return importlib.import_module("pytorch_adapter")
    except ModuleNotFoundError as exc:
        if exc.name != "pytorch_adapter":
            raise
        pytest.fail("#652 RED: pytorch_adapter capability is not implemented", pytrace=False)


def deny_import(attempts):
    def importer():
        attempts.append("torch")
        raise AssertionError("Optional framework must not be loaded here")

    return importer


def empty_registry(monkeypatch):
    from tool_registry import ToolRegistry

    # Keep real registration, schema and execution; omit unrelated tool discovery.
    monkeypatch.setattr(ToolRegistry, "_load_all", lambda self: None)
    return ToolRegistry()


def executor_for(module, monkeypatch, adapter):
    from universal_tool_platform import UniversalToolExecutor

    registry = empty_registry(monkeypatch)
    module.register_pytorch_tools(registry, adapter=adapter)
    return registry, UniversalToolExecutor(registry)


def invoke(executor, name, arguments=None, *, trace=None, runtime_id="runtime-a"):
    from universal_tool_platform import UniversalToolCall

    call = UniversalToolCall(
        tool_name=name,
        arguments={} if arguments is None else arguments,
        transport="mcp",
        call_id="call-pytorch-652",
        trace_id="trace-pytorch-652",
        invocation_id="invocation-pytorch-652",
        user_id="owner-652",
        metadata={"runtime_id": runtime_id},
    )
    return executor.execute_with_trace(call, trace)


class TinyTensor:
    def __init__(self, values, torch):
        self.values = list(values)
        self.torch = torch

    def __mul__(self, factor):
        self.torch.multiplications.append(factor)
        return TinyTensor([value * factor for value in self.values], self.torch)

    def mul(self, factor):
        return self * factor

    def tolist(self):
        return list(self.torch.output_override or self.values)


class TinyTorch:
    """Only the bounded public tensor surface needed by the proposed smoke."""

    __version__ = "contract-fake"
    float32 = object()
    version = SimpleNamespace(cuda=None)

    def __init__(self):
        self.allocations = []
        self.multiplications = []
        self.output_override = None
        self.failure = None

    def tensor(self, values, *, device, dtype, **kwargs):
        assert device == "cpu", "No default or accelerator device"
        assert dtype is self.float32, "Do not inherit process-global dtype"
        assert kwargs.get("requires_grad", False) is False
        assert list(values) == [1, 2, 3], "Allocation must be fixed and tiny"
        self.allocations.append(list(values))
        if self.failure:
            raise self.failure
        return TinyTensor(values, self)

    def inference_mode(self):
        return nullcontext()

    def no_grad(self):
        return nullcontext()

    def __getattr__(self, name):
        raise AssertionError(f"Unexpected framework operation: {name}")


def test_adapter_is_lazy_on_cold_import_and_disabled_calls():
    if importlib.util.find_spec("pytorch_adapter") is None:
        pytest.fail("#652 RED: pytorch_adapter capability is not implemented", pytrace=False)
    script = textwrap.dedent(
        """
        import importlib.abc
        import sys
        import socket
        sys.path.insert(0, sys.argv[1])
        attempts = []
        class BlockTorch(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname == 'torch' or fullname.startswith('torch.'):
                    attempts.append(fullname)
                    raise AssertionError('torch imported while disabled')
        def deny_network(*args, **kwargs):
            attempts.append('network')
            raise AssertionError('network access while disabled')
        sys.meta_path.insert(0, BlockTorch())
        socket.create_connection = deny_network
        socket.socket.connect = deny_network
        socket.socket.connect_ex = deny_network
        import pytorch_adapter
        adapter = pytorch_adapter.PyTorchAdapter()
        assert adapter.status()['state'] == 'disabled'
        result = adapter.smoke()
        assert result.get('error') or result.get('success') is False
        assert not attempts, attempts
        assert not any(n == 'torch' or n.startswith('torch.') for n in sys.modules)
        """
    )
    completed = subprocess.run(
        [sys.executable, "-I", "-c", script, str(ROOT)],
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_registration_and_disabled_tools_do_not_load_framework(monkeypatch):
    module = adapter_module()
    attempts = []
    adapter = module.PyTorchAdapter(importer=deny_import(attempts))
    registry, executor = executor_for(module, monkeypatch, adapter)
    assert set(registry.get_available_categories().keys())
    for name in TOOLS:
        definition = registry.get_universal_definition(name)
        assert definition is not None
        schema = definition.input_schema
        assert schema.get("type") == "object"
        assert schema.get("properties") == {}
        assert schema.get("additionalProperties") is False
        assert not schema.get("required")
        assert definition.executor.get("type") == "local"
        assert "mcp" in definition.supported_transports
    status = invoke(executor, "pytorch_status")
    smoke = invoke(executor, "pytorch_smoke")
    assert status["success"] is True
    assert status["data"]["state"] == "disabled"
    assert smoke["success"] is False
    assert smoke["error"]
    assert attempts == []


@pytest.mark.parametrize(
    ("exception", "state"),
    [
        (ModuleNotFoundError("missing torch", name="torch"), "missing"),
        (ModuleNotFoundError("missing native dependency", name="native_dependency"), "unavailable"),
        (ImportError(f"{SENSITIVE} {INTERNAL_PATH}"), "unavailable"),
        (OSError(f"{SENSITIVE} {INTERNAL_PATH}"), "unavailable"),
        (RuntimeError(f"{SENSITIVE} {INTERNAL_PATH}"), "unavailable"),
    ],
    ids=["torch-missing", "transitive-missing", "import-broken", "binary-broken", "runtime-broken"],
)
def test_dependency_failure_is_classified_without_leaking_details(monkeypatch, exception, state):
    module = adapter_module()
    from trace_manager import ExecutionTrace

    def importer():
        raise exception

    adapter = module.PyTorchAdapter(enabled=True, importer=importer)
    _, executor = executor_for(module, monkeypatch, adapter)
    trace = ExecutionTrace("trace-pytorch-652")
    trace.set_context(invocation_id="invocation-pytorch-652")
    status = invoke(executor, "pytorch_status")
    smoke = invoke(executor, "pytorch_smoke", trace=trace)
    assert status["success"] is True
    assert status["data"]["state"] == state
    assert smoke["success"] is False
    assert smoke["error"]
    public = json.dumps([status, smoke, trace.trace], default=str)
    assert SENSITIVE not in public
    assert INTERNAL_PATH not in public
    assert "Traceback (most recent call last)" not in public
    assert len(trace.trace["tool_calls"]) == 1


@pytest.mark.parametrize("name", TOOLS)
@pytest.mark.parametrize(
    "arguments",
    [
        {"url": "https://example.invalid/model"},
        {"path": "/private/model.pt"},
        {"model": "unapproved-model"},
        {"code": "print('must not execute')"},
        {"checkpoint": "weights.pkl"},
        {"shape": [1000000000, 1000000000]},
        {"device": "cuda"},
        {"runtime_id": "runtime-b"},
    ],
    ids=["url", "path", "model", "code", "checkpoint", "allocation", "gpu", "runtime-override"],
)
def test_unknown_arguments_fail_before_loading_torch(monkeypatch, name, arguments):
    module = adapter_module()
    attempts = []
    adapter = module.PyTorchAdapter(enabled=True, importer=deny_import(attempts))
    _, executor = executor_for(module, monkeypatch, adapter)
    result = invoke(executor, name, arguments)
    assert result["success"] is False
    assert result["metadata"]["phase"] == "validation"
    assert attempts == []


def test_ready_smoke_is_bounded_and_result_comes_from_tensor(monkeypatch):
    module = adapter_module()
    torch = TinyTorch()
    adapter = module.PyTorchAdapter(enabled=True, importer=lambda: torch)
    _, executor = executor_for(module, monkeypatch, adapter)
    status = invoke(executor, "pytorch_status")
    assert status["success"] is True
    assert status["data"]["state"] == "ready"
    assert torch.allocations == [], "Status must not allocate a tensor"
    for _ in range(2):
        result = invoke(executor, "pytorch_smoke")
        assert result["success"] is True
        assert result["data"]["device"] == "cpu"
        assert result["data"]["result"] == [2, 4, 6]
    assert torch.allocations == [[1, 2, 3], [1, 2, 3]]
    assert torch.multiplications == [2, 2]
    # A distinct deterministic witness rejects a hard-coded successful answer.
    torch.output_override = [11, 13, 17]
    assert invoke(executor, "pytorch_smoke")["data"]["result"] == [11, 13, 17]


def test_compute_failure_is_safe_in_result_and_real_trace(monkeypatch):
    module = adapter_module()
    from trace_manager import ExecutionTrace

    torch = TinyTorch()
    torch.failure = RuntimeError(f"{SENSITIVE} {INTERNAL_PATH}")
    _, executor = executor_for(
        module, monkeypatch, module.PyTorchAdapter(enabled=True, importer=lambda: torch)
    )
    trace = ExecutionTrace("trace-pytorch-652")
    result = invoke(executor, "pytorch_smoke", trace=trace)
    assert result["success"] is False
    assert result["error"]
    public = json.dumps([result, trace.trace], default=str)
    assert SENSITIVE not in public
    assert INTERNAL_PATH not in public
    assert len(trace.trace["tool_calls"]) == 1


def test_real_trace_records_execution_and_preserves_correlation(monkeypatch):
    module = adapter_module()
    from trace_manager import ExecutionTrace

    torch = TinyTorch()
    _, executor = executor_for(
        module, monkeypatch, module.PyTorchAdapter(enabled=True, importer=lambda: torch)
    )
    trace = ExecutionTrace("trace-pytorch-652")
    trace.set_context(invocation_id="invocation-pytorch-652")
    before_billing = json.dumps(trace.trace["billing"], sort_keys=True)
    result = invoke(executor, "pytorch_smoke", trace=trace)
    assert result["success"] is True
    for key, expected in {
        "call_id": "call-pytorch-652",
        "trace_id": "trace-pytorch-652",
        "invocation_id": "invocation-pytorch-652",
        "runtime_id": "runtime-a",
    }.items():
        assert result["metadata"][key] == expected
    calls = trace.trace["tool_calls"]
    assert len(calls) == 1
    assert calls[0]["call_id"] == "call-pytorch-652"
    assert calls[0]["result"]["data"]["result"] == [2, 4, 6]
    assert json.dumps(trace.trace["billing"], sort_keys=True) == before_billing


def test_runtime_dispatcher_keeps_adapter_instances_and_owners_isolated(monkeypatch):
    module = adapter_module()
    from runtime import RuntimeDispatcher, RuntimeNotFound, RuntimeScopeViolation

    attempts = []
    torch = TinyTorch()
    _, disabled = executor_for(
        module, monkeypatch, module.PyTorchAdapter(importer=deny_import(attempts))
    )
    _, enabled = executor_for(
        module, monkeypatch, module.PyTorchAdapter(enabled=True, importer=lambda: torch)
    )
    executors = {"runtime-a": disabled, "runtime-b": enabled}
    dispatcher = RuntimeDispatcher()
    for runtime_id in executors:
        dispatcher.register_runtime(runtime_id, owner_id="owner-652")
    executed = []

    def handler(context, payload):
        executed.append(context.runtime_id)
        return invoke(executors[context.runtime_id], payload["tool"], runtime_id=context.runtime_id)

    dispatcher.register_operation("contract.pytorch.call", handler)
    for _ in range(2):
        for runtime_id, expected in (("runtime-a", "disabled"), ("runtime-b", "ready")):
            result = dispatcher.dispatch(
                runtime_id,
                "contract.pytorch.call",
                {"tool": "pytorch_status"},
                caller_owner_id="owner-652",
            )
            assert result["data"]["state"] == expected
            assert result["metadata"]["runtime_id"] == runtime_id
    before = len(executed)
    with pytest.raises(RuntimeScopeViolation):
        dispatcher.dispatch(
            "runtime-a",
            "contract.pytorch.call",
            {"tool": "pytorch_smoke"},
            resource_runtime_id="runtime-b",
            caller_owner_id="owner-652",
        )
    with pytest.raises(RuntimeScopeViolation):
        dispatcher.dispatch(
            "runtime-b",
            "contract.pytorch.call",
            {"tool": "pytorch_smoke"},
            caller_owner_id="other-owner",
        )
    with pytest.raises(RuntimeNotFound):
        dispatcher.dispatch("unknown", "contract.pytorch.call", {"tool": "pytorch_smoke"})
    assert len(executed) == before
    assert attempts == []
    assert torch.allocations == []

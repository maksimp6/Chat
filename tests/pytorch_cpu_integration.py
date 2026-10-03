"""Explicit CPU job: python -m pytest -q tests/pytorch_cpu_integration.py.

Not named test_*.py: ordinary CI must not require torch. Explicit invocation
MUST fail, not skip, when the optional CPU framework is absent or broken.
The implementation PR must wire this command to its pinned CPU-only job.
"""

import importlib
import socket


def test_real_cpu_tensor_smoke(monkeypatch):
    # These are intentionally real imports. Do not use importorskip or xfail.
    torch = importlib.import_module("torch")
    module = importlib.import_module("pytorch_adapter")
    from tool_registry import ToolRegistry
    from trace_manager import ExecutionTrace
    from universal_tool_platform import UniversalToolCall, UniversalToolExecutor

    assert torch.version.cuda is None, "The dedicated job must install a CPU-only wheel"
    monkeypatch.setattr(ToolRegistry, "_load_all", lambda self: None)
    registry = ToolRegistry()
    allocations = []
    real_tensor = torch.tensor

    def observed_tensor(data, *args, **kwargs):
        tensor = real_tensor(data, *args, **kwargs)
        allocations.append(tensor)
        assert tensor.device.type == "cpu"
        assert tensor.dtype == torch.float32
        assert tensor.numel() == 3
        assert tensor.requires_grad is False
        return tensor

    monkeypatch.setattr(torch, "tensor", observed_tensor)

    def forbidden_side_effect(*args, **kwargs):
        raise AssertionError("Smoke must not access network or alter global settings")

    monkeypatch.setattr(socket, "create_connection", forbidden_side_effect)
    monkeypatch.setattr(socket.socket, "connect", forbidden_side_effect)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden_side_effect)
    restore_dtype = torch.set_default_dtype
    restore_device = torch.set_default_device
    old_dtype = torch.get_default_dtype()
    old_device = torch.get_default_device()
    try:
        # Make implicit defaults wrong, then prove the adapter does not mutate them.
        torch.set_default_dtype(torch.float64)
        torch.set_default_device("meta")
        for name in (
            "set_default_dtype",
            "set_default_device",
            "set_num_threads",
            "set_num_interop_threads",
            "set_default_tensor_type",
            "manual_seed",
            "use_deterministic_algorithms",
        ):
            monkeypatch.setattr(torch, name, forbidden_side_effect)
        module.register_pytorch_tools(registry, adapter=module.PyTorchAdapter(enabled=True))
        trace = ExecutionTrace("real-pytorch-cpu-652")
        executor = UniversalToolExecutor(registry)
        for index in range(2):
            call = UniversalToolCall(
                tool_name="pytorch_smoke",
                arguments={},
                transport="mcp",
                call_id=f"real-smoke-{index}",
                trace_id=trace.trace_id,
                invocation_id="real-cpu-invocation-652",
                metadata={"runtime_id": "cpu-test-runtime"},
            )
            result = executor.execute_with_trace(call, trace)
            assert result["success"] is True, result
            assert result["data"]["device"] == "cpu"
            assert result["data"]["result"] == [2, 4, 6]
        assert len(allocations) == 2
        assert len(trace.trace["tool_calls"]) == 2
        assert torch.get_default_dtype() == torch.float64
        assert torch.get_default_device().type == "meta"
    finally:
        restore_device(old_device)
        restore_dtype(old_dtype)

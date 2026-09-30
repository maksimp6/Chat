"""Optional CPU-only PyTorch foundation for Alice Pro (#652).

Opt-in only — torch is never imported during normal startup.
See docs/pytorch-cpu-foundation.md for installation and rollback.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Optional

logger = logging.getLogger("pytorch_adapter")


def _default_importer() -> Any:
    import torch  # noqa: PLC0415

    return torch


def _call_metadata(call: Any) -> dict[str, Any]:
    if call is None:
        return {}
    return {
        "call_id": call.call_id,
        "trace_id": call.trace_id,
        "invocation_id": call.invocation_id,
        "runtime_id": dict(call.metadata or {}).get("runtime_id"),
    }


_TOOL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}

_TOOL_TRANSPORTS = ["responses_api", "local_agent", "mcp"]


class PyTorchAdapter:
    """Lazy optional CPU-only PyTorch adapter.

    States: disabled → (enabled) → missing / unavailable / ready.
    The importer is trusted dependency injection; it is never a tool argument.
    """

    def __init__(
        self,
        enabled: bool = False,
        importer: Optional[Callable[[], Any]] = None,
    ) -> None:
        self._enabled = enabled
        self._importer = importer
        self._torch: Any = None
        self._state = "disabled"
        self._tried = False
        self._lock = threading.Lock()

    def _ensure_loaded(self) -> None:
        if not self._enabled or self._tried:
            return
        with self._lock:
            if self._tried:
                return
            self._tried = True
            importer = self._importer if self._importer is not None else _default_importer
            try:
                self._torch = importer()
                self._state = "ready"
            except ModuleNotFoundError as exc:
                if getattr(exc, "name", None) == "torch":
                    self._state = "missing"
                else:
                    self._state = "unavailable"
                logger.debug("pytorch_adapter: dependency error: %s", type(exc).__name__)
            except (ImportError, OSError, RuntimeError) as exc:
                self._state = "unavailable"
                logger.debug("pytorch_adapter: load error: %s", type(exc).__name__)

    def status(self) -> dict[str, Any]:
        """Return adapter state without leaking import errors."""
        self._ensure_loaded()
        return {"state": self._state}

    def smoke(self) -> dict[str, Any]:
        """Run a fixed tiny CPU float32 smoke: tensor([1,2,3]) × 2 = [2,4,6]."""
        self._ensure_loaded()
        if self._state != "ready":
            return {"success": False, "error": f"pytorch not available: {self._state}"}
        torch = self._torch
        try:
            with torch.inference_mode():
                t = torch.tensor(
                    [1, 2, 3], device="cpu", dtype=torch.float32, requires_grad=False
                )
                output = t.mul(2).tolist()
            return {"success": True, "data": {"device": "cpu", "result": output}}
        except Exception as exc:
            logger.debug("pytorch_adapter: smoke error: %s", type(exc).__name__)
            return {"success": False, "error": f"pytorch smoke error: {type(exc).__name__}"}


def register_pytorch_tools(
    registry: Any,
    *,
    adapter: Optional[PyTorchAdapter] = None,
) -> None:
    """Register pytorch_status and pytorch_smoke into registry under 'pytorch'."""
    if adapter is None:
        adapter = PyTorchAdapter()

    def _status(arguments: dict, cfg: Optional[dict] = None) -> dict[str, Any]:
        context = (cfg or {}).get("_universal_context") or {}
        call = context.get("call")
        return {"success": True, "data": adapter.status(), "metadata": _call_metadata(call)}

    def _smoke(arguments: dict, cfg: Optional[dict] = None) -> dict[str, Any]:
        context = (cfg or {}).get("_universal_context") or {}
        call = context.get("call")
        result = adapter.smoke()
        metadata = _call_metadata(call)
        if result.get("success"):
            return {"success": True, "data": result["data"], "metadata": metadata}
        return {
            "success": False,
            "error": result.get("error", "pytorch smoke failed"),
            "metadata": metadata,
        }

    registry.register(
        "pytorch",
        "pytorch_status",
        {
            "description": "Report the PyTorch adapter state (disabled/missing/unavailable/ready).",
            "input_schema": _TOOL_SCHEMA,
            "read_only": True,
            "requires_approval": False,
            "supported_transports": _TOOL_TRANSPORTS,
            "executor": {"type": "local"},
            "func": _status,
        },
    )
    registry.register(
        "pytorch",
        "pytorch_smoke",
        {
            "description": "Fixed CPU float32 smoke: tensor([1,2,3]) × 2 = [2,4,6].",
            "input_schema": _TOOL_SCHEMA,
            "read_only": True,
            "requires_approval": False,
            "supported_transports": _TOOL_TRANSPORTS,
            "executor": {"type": "local"},
            "func": _smoke,
        },
    )

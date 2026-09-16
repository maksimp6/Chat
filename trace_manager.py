"""Centralized execution trace for the Alice Pro chat pipeline."""
import json
import time
import uuid
import traceback
from typing import Any, Dict, Optional

from trace_security import MAX_DEPTH, MAX_ITEMS, MAX_REPR, SENSITIVE_KEYS, safe_repr, sanitize_trace_value


class ExecutionTrace:
    SCHEMA_VERSION = 1
    _SENSITIVE_KEYS = SENSITIVE_KEYS
    _MAX_REPR = MAX_REPR
    _MAX_DEPTH = MAX_DEPTH
    _MAX_ITEMS = MAX_ITEMS
    _INTERNAL_EVENT_TYPES = {"trace_finalized"}

    def __init__(self, trace_id: Optional[str] = None):
        self.trace_id = trace_id or str(uuid.uuid4())
        self.start_perf = time.perf_counter()
        self._finalized = False
        self.trace: Dict[str, Any] = {
            "trace_id": self.trace_id,
            "schema_version": self.SCHEMA_VERSION,
            "created_at": time.time(),
            "request": {},
            "responses": [],
            "tool_calls": [],
            "events": [],
            "timings": {},
            "errors": [],
            "billing": {
                "currency": "RUB",
                "provider": "yandex_ai_studio",
                "pricing_version": "config-v1",
                "items": []
            }
        }

    def get_metadata(self) -> Dict[str, str]:
        return {"trace_id": str(self.trace_id)}

    def set_context(self, invocation_id: Optional[str] = None,
                    session_id: Optional[str] = None,
                    conversation_id: Optional[str] = None) -> None:
        context = self.trace.setdefault("context", {})
        for key, value in (("invocation_id", invocation_id), ("session_id", session_id),
                           ("conversation_id", conversation_id), ("trace_id", self.trace_id)):
            if value is not None:
                context[key] = str(value)
        billing = self.trace.setdefault("billing", {})
        for key in ("invocation_id", "session_id", "conversation_id", "trace_id"):
            if key in context:
                billing[key] = context[key]

    def set_request(self, payload: Dict[str, Any]) -> None:
        clean_payload = {k: v for k, v in payload.items()
                         if k not in ("trace", "execution_trace")} if isinstance(payload, dict) else payload
        self.trace["request"] = clean_payload
        self.add_event("request_initialized", {
            "keys": list(clean_payload.keys()) if isinstance(clean_payload, dict) else []
        })

    @classmethod
    def _safe_repr(cls, value: Any, depth: int = 0) -> Any:
        return safe_repr(value, depth)

    @classmethod
    def _capture_exception_state(cls, exc: BaseException) -> Dict[str, Any]:
        frames = []
        tb = exc.__traceback__
        for frame, lineno in traceback.walk_tb(tb):
            locals_snapshot = {}
            for name, value in frame.f_locals.items():
                if name.lower() in cls._SENSITIVE_KEYS or any(secret in name.lower() for secret in ("api_key", "password", "secret", "token")):
                    locals_snapshot[name] = "<redacted>"
                else:
                    locals_snapshot[name] = cls._safe_repr(value)
            frames.append({"file": frame.f_code.co_filename, "function": frame.f_code.co_name,
                           "line": lineno, "locals": locals_snapshot})
        return {"exception_type": type(exc).__name__, "exception_message": str(exc),
                "traceback": traceback.format_exception(type(exc), exc, exc.__traceback__), "frames": frames}

    def _request_timing_for_step(self, step_index: int) -> tuple[Optional[float], Optional[float]]:
        start = None
        api_requests = self.trace.get("api_requests", [])
        for request in reversed(api_requests):
            if request.get("step") == step_index:
                value = request.get("timestamp")
                if isinstance(value, (int, float)):
                    start = float(value)
                break
        for event in reversed(self.trace.get("events", [])):
            if event.get("type") != "api_request_completed":
                continue
            payload = event.get("payload") or {}
            if payload.get("step") != step_index:
                continue
            value = payload.get("start_timestamp")
            if start is None and isinstance(value, (int, float)):
                start = float(value)
            break
        return start, None

    def _infer_response_start(self, step_index: int, end_timestamp: float) -> Optional[float]:
        request_start, _ = self._request_timing_for_step(step_index)
        if request_start is not None:
            return request_start
        if self.trace["responses"]:
            previous = self.trace["responses"][-1]
            previous_end = previous.get("end_timestamp", previous.get("timestamp"))
            if isinstance(previous_end, (int, float)):
                return float(previous_end)
        created = self.trace.get("created_at")
        if isinstance(created, (int, float)):
            return float(created)
        return None

    def add_api_request(self, payload: Dict[str, Any], step_index: int = 1,
                        start_timestamp: Optional[float] = None) -> int:
        request_entry = {"step": step_index,
                         "timestamp": start_timestamp if start_timestamp is not None else time.time(),
                         "payload": self._sanitize_trace_value(payload)}
        self.trace.setdefault("api_requests", []).append(request_entry)
        self.add_event("api_request_registered", {"step": step_index, "timestamp": request_entry["timestamp"]})
        return len(self.trace["api_requests"]) - 1

    @classmethod
    def _sanitize_trace_value(cls, value: Any, depth: int = 0) -> Any:
        return sanitize_trace_value(value, depth)

    def _update_billing_for_response(self, clean_raw: Dict[str, Any], step_index: int,
                                     response_id: Optional[str]) -> None:
        if not isinstance(clean_raw, dict) or not isinstance(clean_raw.get("usage"), dict):
            return
        try:
            from billing import build_ai_billing_item, aggregate_billing
            model = clean_raw.get("model")
            if not model:
                for request in reversed(self.trace.get("api_requests", [])):
                    if request.get("step") == step_index:
                        payload = request.get("payload") or {}
                        model_uri = payload.get("model")
                        if isinstance(model_uri, str):
                            model = model_uri.rsplit("/", 2)[-2] if model_uri.count("/") >= 2 else model_uri
                        break
            item = build_ai_billing_item(model, clean_raw.get("usage"), step_index, response_id)
            items = self.trace.setdefault("billing", {}).setdefault("items", [])
            key = (item.get("step"), item.get("response_id"))
            existing = next((i for i, old in enumerate(items)
                             if (old.get("step"), old.get("response_id")) == key), None)
            if existing is None:
                items.append(item)
            else:
                items[existing] = item
            context = self.trace.get("context", {})
            self.trace["billing"] = aggregate_billing(items, context)
        except Exception as exc:
            self.add_event("billing_error", {"step": step_index, "error": str(exc)})

    def add_response(self, raw_json: Dict[str, Any], step_index: int = 1,
                     start_timestamp: Optional[float] = None,
                     end_timestamp: Optional[float] = None,
                     timing_ms: Optional[float] = None,
                     kind: str = "response", deduplicate: bool = False, **kwargs) -> None:
        """Store one logical Responses API operation; polling snapshots stay nested in it."""
        idx = step_index or kwargs.get("call_index", 1)

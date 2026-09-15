"""Centralized execution trace for the Alice Pro chat pipeline."""
import json
import time
import uuid
import traceback
from typing import Any, Dict, Optional


class ExecutionTrace:
    SCHEMA_VERSION = 1
    _SENSITIVE_KEYS = {
        "api_key", "apikey", "authorization", "password", "passwd", "secret",
        "token", "access_token", "refresh_token", "cookie", "set-cookie"
    }
    _MAX_REPR = 4000
    _MAX_DEPTH = 3
    _MAX_ITEMS = 50

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
            "errors": []
        }

    def get_metadata(self) -> Dict[str, str]:
        return {"trace_id": str(self.trace_id)}

    def set_request(self, payload: Dict[str, Any]) -> None:
        clean_payload = {k: v for k, v in payload.items()
                         if k not in ("trace", "execution_trace")} if isinstance(payload, dict) else payload
        self.trace["request"] = clean_payload
        self.add_event("request_initialized", {
            "keys": list(clean_payload.keys()) if isinstance(clean_payload, dict) else []
        })

    @classmethod
    def _safe_repr(cls, value: Any, depth: int = 0) -> Any:
        """Make exception locals JSON-safe without leaking common credentials."""
        if depth > cls._MAX_DEPTH:
            return "<max-depth>"
        if value is None or isinstance(value, (bool, int, float, str)):
            if isinstance(value, str) and len(value) > cls._MAX_REPR:
                return value[:cls._MAX_REPR] + "... <truncated>"
            return value
        if isinstance(value, dict):
            result = {}
            for index, (key, item) in enumerate(value.items()):
                if index >= cls._MAX_ITEMS:
                    result["<truncated>"] = f"{len(value) - cls._MAX_ITEMS} more items"
                    break
                key_text = str(key)
                if key_text.lower().replace("-", "_") in cls._SENSITIVE_KEYS:
                    result[key_text] = "<redacted>"
                else:
                    result[key_text] = cls._safe_repr(item, depth + 1)
            return result
        if isinstance(value, (list, tuple, set)):
            values = list(value)
            result = [cls._safe_repr(item, depth + 1) for item in values[:cls._MAX_ITEMS]]
            if len(values) > cls._MAX_ITEMS:
                result.append(f"<truncated: {len(values) - cls._MAX_ITEMS} more items>")
            return result
        try:
            text = repr(value)
        except Exception:
            text = f"<{type(value).__name__}: repr failed>"
        if len(text) > cls._MAX_REPR:
            text = text[:cls._MAX_REPR] + "... <truncated>"
        return text

    @classmethod
    def _capture_exception_state(cls, exc: BaseException) -> Dict[str, Any]:
        """Capture traceback frames and the local variables visible at the failure."""
        frames = []
        tb = exc.__traceback__
        for frame, lineno in traceback.walk_tb(tb):
            locals_snapshot = {}
            for name, value in frame.f_locals.items():
                if name.lower() in cls._SENSITIVE_KEYS or any(secret in name.lower() for secret in ("api_key", "password", "secret", "token")):
                    locals_snapshot[name] = "<redacted>"
                else:
                    locals_snapshot[name] = cls._safe_repr(value)
            frames.append({
                "file": frame.f_code.co_filename,
                "function": frame.f_code.co_name,
                "line": lineno,
                "locals": locals_snapshot
            })

        return {
            "exception_type": type(exc).__name__,
            "exception_message": str(exc),
            "traceback": traceback.format_exception(type(exc), exc, exc.__traceback__),
            "frames": frames
        }

    def _request_timing_for_step(self, step_index: int) -> tuple[Optional[float], Optional[float]]:
        """Return the real request start; the response end is captured when the final response is received."""
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
        """Fallback inference only. Prefer the real API request timestamp."""
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
        """Store the exact request payload for a Responses API step."""
        request_entry = {
            "step": step_index,
            "timestamp": start_timestamp if start_timestamp is not None else time.time(),
            "payload": self._sanitize_trace_value(payload)
        }
        self.trace.setdefault("api_requests", []).append(request_entry)
        self.add_event("api_request_registered", {
            "step": step_index,
            "timestamp": request_entry["timestamp"]
        })
        return len(self.trace["api_requests"]) - 1

    @classmethod
    def _sanitize_trace_value(cls, value: Any, depth: int = 0) -> Any:
        """Deep-copy trace payloads while removing credentials and cyclic references."""
        if depth > cls._MAX_DEPTH:
            return "<max-depth>"
        if value is None or isinstance(value, (bool, int, float, str)):
            if isinstance(value, str) and len(value) > cls._MAX_REPR:
                return value[:cls._MAX_REPR] + "... <truncated>"
            return value
        if isinstance(value, dict):
            result = {}
            for key, item in list(value.items())[:cls._MAX_ITEMS]:
                key_text = str(key)
                normalized = key_text.lower().replace("-", "_")
                if normalized in cls._SENSITIVE_KEYS:
                    result[key_text] = "<redacted>"
                else:
                    result[key_text] = cls._sanitize_trace_value(item, depth + 1)
            if len(value) > cls._MAX_ITEMS:
                result["<truncated>"] = f"{len(value) - cls._MAX_ITEMS} more items"
            return result
        if isinstance(value, (list, tuple, set)):
            items = list(value)
            result = [cls._sanitize_trace_value(item, depth + 1) for item in items[:cls._MAX_ITEMS]]
            if len(items) > cls._MAX_ITEMS:
                result.append(f"<truncated: {len(items) - cls._MAX_ITEMS} more items>")
            return result
        return cls._safe_repr(value, depth)

    def add_response(
        self,
        raw_json: Dict[str, Any],
        step_index: int = 1,
        start_timestamp: Optional[float] = None,
        end_timestamp: Optional[float] = None,
        timing_ms: Optional[float] = None,
        kind: str = "response",
        deduplicate: bool = False,
        **kwargs
    ) -> None:
        """Store a complete sanitized Responses API JSON response."""
        idx = step_index or kwargs.get("call_index", 1)
        clean_raw = self._sanitize_trace_value(raw_json)

        request_start, _ = self._request_timing_for_step(idx)
        if start_timestamp is None:
            start_timestamp = request_start
        if end_timestamp is None:
            end_timestamp = time.time()
        if start_timestamp is None:
            start_timestamp = self._infer_response_start(idx, end_timestamp)
        if timing_ms is None and start_timestamp is not None:
            timing_ms = round(max(0.0, end_timestamp - start_timestamp) * 1000, 2)

        if deduplicate and self.trace["responses"]:
            last = self.trace["responses"][-1]
            if last.get("step") == idx and last.get("raw") == clean_raw and last.get("kind") == kind:
                return

        response_entry = {
            "step": idx,
            "timestamp": end_timestamp,
            "kind": kind,
            "response_id": clean_raw.get("id") if isinstance(clean_raw, dict) else None,
            "raw": clean_raw,
            "start_timestamp": start_timestamp,
            "end_timestamp": end_timestamp,
            "timing_ms": timing_ms
        }

        api_requests = self.trace.get("api_requests", [])
        for request_entry in reversed(api_requests):
            if request_entry.get("step") == idx:
                response_entry["request"] = request_entry.get("payload")
                break

        self.trace["responses"].append(response_entry)

        self.add_event("api_response_received", {
            "step": idx,
            "kind": kind,
            "response_id": response_entry.get("response_id"),
            "status": clean_raw.get("status") if isinstance(clean_raw, dict) else None,
            "has_output": bool(clean_raw.get("output")) if isinstance(clean_raw, dict) else False,
            "start_timestamp": start_timestamp,
            "end_timestamp": end_timestamp,
            "timing_ms": timing_ms
        })

    def track_tool_execution(self, name: str, arguments: Dict[str, Any], executor_fn, *args,
                             call_id: Optional[str] = None, parent_id: Optional[str] = None,
                             step: Optional[int] = None, server: Optional[str] = None, **kwargs) -> Any:
        start_timestamp = time.time()
        started = time.perf_counter()
        error = None
        result = None
        try:
            result = executor_fn(*args, **kwargs)
            return result
        except Exception as exc:
            error = str(exc)
            self.record_error(f"tool:{name}", error, call_id=call_id, parent_id=parent_id, step=step,
                              exception=exc)
            raise
        finally:
            end_timestamp = time.time()
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            entry = {
                "name": name, "arguments": self._sanitize_trace_value(arguments),
                "result": self._sanitize_trace_value(result), "error": error,
                "timing_ms": elapsed_ms, "timestamp": end_timestamp,
                "start_timestamp": start_timestamp, "end_timestamp": end_timestamp
            }
            if call_id is not None: entry["call_id"] = str(call_id)
            if parent_id is not None: entry["parent_id"] = str(parent_id)
            if step is not None: entry["step"] = step
            if server is not None: entry["server"] = server
            self.trace["tool_calls"].append(entry)
            self.add_event("tool_executed", {
                "name": name, "timing_ms": elapsed_ms, "success": error is None,
                "start_timestamp": start_timestamp, "end_timestamp": end_timestamp,
                **({"call_id": str(call_id)} if call_id is not None else {}),
                **({"parent_id": str(parent_id)} if parent_id is not None else {}),
                **({"step": step} if step is not None else {})
            })

    def add_event(self, event_type: str, payload: Optional[Dict[str, Any]] = None) -> None:
        self.trace["events"].append({"type": event_type, "timestamp": time.time(), "payload": payload or {}})

    def record_error(self, source: str, message: str, call_id: Optional[str] = None,
                     parent_id: Optional[str] = None, step: Optional[int] = None,
                     error_type: Optional[str] = None, exception: Optional[BaseException] = None) -> None:
        entry = {"source": source, "error": message, "timestamp": time.time()}
        if error_type: entry["type"] = error_type
        if call_id is not None: entry["call_id"] = str(call_id)
        if parent_id is not None: entry["parent_id"] = str(parent_id)
        if step is not None: entry["step"] = step
        if exception is not None:
            entry["python_exception"] = self._capture_exception_state(exception)
        self.trace["errors"].append(entry)
        event_payload = {"source": source, "error": message}
        if call_id is not None: event_payload["call_id"] = str(call_id)
        if parent_id is not None: event_payload["parent_id"] = str(parent_id)
        if step is not None: event_payload["step"] = step
        if exception is not None:
            event_payload["exception_type"] = type(exception).__name__
            event_payload["has_python_state"] = True
        self.add_event("error_occurred", event_payload)

    def finalize(self) -> Dict[str, Any]:
        if not self._finalized:
            total_ms = round((time.perf_counter() - self.start_perf) * 1000, 2)
            self.trace["timings"]["total_duration_ms"] = total_ms
            self.add_event("trace_finalized", {"total_duration_ms": total_ms})
            self._finalized = True

        def _json_default(obj):
            if isinstance(obj, ExecutionTrace):
                return {"trace_id": obj.trace_id, "<circular_ref>": True}
            try:
                return self._safe_repr(obj)
            except Exception:
                return f"<{type(obj).__name__}: safe_repr failed>"

        try:
            return json.loads(json.dumps(self.trace, default=_json_default))
        except Exception:
            return {
                "trace_id": self.trace_id,
                "schema_version": self.SCHEMA_VERSION,
                "error": "trace_serialization_failed",
                "timings": self.trace.get("timings", {}),
                "errors": [str(e) for e in self.trace.get("errors", [])],
            }

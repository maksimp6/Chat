"""Centralized execution trace for the Alice Pro chat pipeline."""
import json
import time
import uuid
import traceback
from contextvars import ContextVar
from functools import wraps
from typing import Any, Dict, Optional, Callable

_current_trace: ContextVar["ExecutionTrace | None"] = ContextVar("alice_execution_trace", default=None)

from trace_security import MAX_DEPTH, MAX_ITEMS, MAX_REPR, SENSITIVE_KEYS, safe_repr, sanitize_trace_value
from trace_timing import infer_response_start, request_timing_for_step, step_correlation_id
from trace_timing import infer_response_start, request_timing_for_step, step_correlation_id



def get_current_trace() -> Optional["ExecutionTrace"]:
    """Return the trace active for the current request/task, if any."""
    return _current_trace.get()


def traced_operation(operation: str, *, include_request: bool = True):
    """Trace an important non-chat operation, including its final HTTP outcome."""
    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(fn)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            trace = ExecutionTrace()
            trace.set_context(invocation_id=trace.trace_id)
            payload = {
                "operation": operation,
                "method": getattr(kwargs.get("request"), "method", None),
            }
            if include_request:
                try:
                    from flask import request as flask_request
                    payload.update({
                        "method": flask_request.method,
                        "path": flask_request.path,
                    })
                    if flask_request.is_json:
                        payload["body"] = flask_request.get_json(silent=True) or {}
                    elif flask_request.form:
                        payload["form_keys"] = sorted(flask_request.form.keys())
                except Exception:
                    pass
            trace.set_request(payload)
            trace.add_event("operation_started", {"operation": operation})
            token = _current_trace.set(trace)
            try:
                result = fn(*args, **kwargs)
                try:
                    from flask import make_response
                    response = make_response(result)
                    trace.add_event("operation_completed", {
                        "operation": operation,
                        "http_status": response.status_code,
                        "success": response.status_code < 400,
                    })
                except Exception as exc:
                    trace.record_error(operation, str(exc), exception=exc)
                return result
            except Exception as exc:
                trace.record_error(operation, str(exc), exception=exc)
                raise
            finally:
                _current_trace.reset(token)
                trace.finalize()
        return wrapped
    return decorator


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
            },
            "provider_key": None,
            "provider_keys": []
        }

    def get_metadata(self) -> Dict[str, str]:
        return {"trace_id": str(self.trace_id)}

    def set_provider_key(
        self,
        key_id: str,
        *,
        fingerprint: Optional[str] = None,
        issued_at: Optional[Any] = None,
        expires_at: Optional[Any] = None,
        project_id: Optional[str] = None,
        source: Optional[str] = None,
    ) -> None:
        """Bind a non-secret provider-key identity to this trace."""
        entry = {"key_id": str(key_id)}
        for name, value in (
            ("fingerprint", fingerprint),
            ("issued_at", issued_at),
            ("expires_at", expires_at),
            ("project_id", project_id),
            ("source", source),
        ):
            if value is not None:
                entry[name] = str(value)

        history = self.trace.setdefault("provider_keys", [])
        if entry not in history:
            history.append(entry)
        self.trace["provider_key"] = entry

        context = self.trace.setdefault("context", {})
        context["provider_key_id"] = str(key_id)
        billing = self.trace.setdefault("billing", {})
        billing["provider_key_id"] = str(key_id)
        self.add_event("provider_key_selected", {
            "key_id": str(key_id),
            **({"source": source} if source is not None else {}),
        })

    def set_context(self, invocation_id: Optional[str] = None,
                    session_id: Optional[str] = None,
                    conversation_id: Optional[str] = None,
                    user_id: Optional[str] = None) -> None:
        context = self.trace.setdefault("context", {})
        for key, value in (("invocation_id", invocation_id), ("session_id", session_id),
                           ("conversation_id", conversation_id), ("user_id", user_id),
                           ("owner_id", user_id), ("trace_id", self.trace_id)):
            if value is not None:
                context[key] = str(value)
        billing = self.trace.setdefault("billing", {})
        for key in ("invocation_id", "session_id", "conversation_id", "trace_id", "owner_id"):
            if key in context:
                billing[key] = context[key]

    def set_request(self, payload: Dict[str, Any]) -> None:
        clean_payload = {
            k: v for k, v in payload.items()
            if k not in ("trace", "execution_trace")
        } if isinstance(payload, dict) else payload
        clean_payload = self._sanitize_trace_value(clean_payload)
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
        return request_timing_for_step(self.trace, step_index)

    def _infer_response_start(self, step_index: int, end_timestamp: float) -> Optional[float]:
        return infer_response_start(self.trace, step_index, end_timestamp)

    def get_step_correlation_id(self, step_index: int) -> str:
        """Return a stable correlation identifier for one logical API step."""
        return step_correlation_id(self.trace_id, step_index)

    def add_api_request(self, payload: Dict[str, Any], step_index: int = 1,
                        start_timestamp: Optional[float] = None,
                        provider_key_id: Optional[str] = None) -> int:
        provider_key_id = provider_key_id or (
            self.trace.get("provider_key") or {}
        ).get("key_id")
        correlation_id = self.get_step_correlation_id(step_index)
        request_entry = {"step": step_index,
                         "correlation_id": correlation_id,
                         "timestamp": start_timestamp if start_timestamp is not None else time.time(),
                         "payload": self._sanitize_trace_value(payload)}
        if provider_key_id is not None:
            request_entry["provider_key_id"] = str(provider_key_id)
        self.trace.setdefault("api_requests", []).append(request_entry)
        self.add_event("api_request_registered", {
            "step": step_index,
            "correlation_id": correlation_id,
            "timestamp": request_entry["timestamp"],
        })
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
                     kind: str = "response", deduplicate: bool = False,
                     provider_key_id: Optional[str] = None, **kwargs) -> None:
        """Store one logical Responses API operation; polling snapshots stay nested in it."""
        idx = step_index or kwargs.get("call_index", 1)
        provider_key_id = provider_key_id or (
            self.trace.get("provider_key") or {}
        ).get("key_id")
        clean_raw = self._sanitize_trace_value(raw_json)
        response_id = clean_raw.get("id") if isinstance(clean_raw, dict) else None
        request_start, _ = self._request_timing_for_step(idx)
        if start_timestamp is None:
            start_timestamp = request_start
        if end_timestamp is None:
            end_timestamp = time.time()
        if start_timestamp is None:
            start_timestamp = self._infer_response_start(idx, end_timestamp)
        if timing_ms is None and start_timestamp is not None:
            timing_ms = round(max(0.0, end_timestamp - start_timestamp) * 1000, 2)

        existing_index = None
        if response_id:
            for i in range(len(self.trace["responses"]) - 1, -1, -1):
                candidate = self.trace["responses"][i]
                if candidate.get("step") == idx and candidate.get("response_id") == response_id:
                    existing_index = i
                    break

        if existing_index is None and deduplicate and self.trace["responses"]:
            last = self.trace["responses"][-1]
            if last.get("step") == idx and last.get("raw") == clean_raw and last.get("kind") == kind:
                return

        if existing_index is not None:
            entry = self.trace["responses"][existing_index]
            if entry.get("start_timestamp") is not None:
                start_timestamp = entry["start_timestamp"]
            snapshots = entry.setdefault("poll_snapshots", [])
            previous_raw = entry.get("raw")
            if not snapshots and previous_raw is not None:
                snapshots.append(previous_raw)
            if not snapshots or snapshots[-1] != clean_raw:
                snapshots.append(clean_raw)
            entry.update({"timestamp": end_timestamp, "raw": clean_raw,
                          "correlation_id": self.get_step_correlation_id(idx),
                          "response_id": response_id or entry.get("response_id"),
                          "end_timestamp": end_timestamp, "timing_ms": timing_ms,
                          "latest_kind": kind})
            if provider_key_id is not None:
                entry["provider_key_id"] = str(provider_key_id)
            self._update_billing_for_response(clean_raw, idx, response_id or entry.get("response_id"))
            return

        correlation_id = self.get_step_correlation_id(idx)
        response_entry = {"step": idx, "correlation_id": correlation_id, "timestamp": end_timestamp, "kind": kind,
                          "response_id": response_id, "raw": clean_raw,
                          "start_timestamp": start_timestamp, "end_timestamp": end_timestamp,
                          "timing_ms": timing_ms}
        if provider_key_id is not None:
            response_entry["provider_key_id"] = str(provider_key_id)
        if kind == "poll_response":
            response_entry["poll_snapshots"] = [clean_raw]
        api_requests = self.trace.get("api_requests", [])
        for request_entry in reversed(api_requests):
            if request_entry.get("step") == idx:
                response_entry["request"] = request_entry.get("payload")
                break
        self.trace["responses"].append(response_entry)
        self.add_event("api_response_received", {
            "step": idx, "correlation_id": correlation_id, "kind": kind, "response_id": response_id,
            "status": clean_raw.get("status") if isinstance(clean_raw, dict) else None,
            "has_output": bool(clean_raw.get("output")) if isinstance(clean_raw, dict) else False,
            "start_timestamp": start_timestamp, "end_timestamp": end_timestamp, "timing_ms": timing_ms})
        self._update_billing_for_response(clean_raw, idx, response_id)

    def track_tool_execution(self, name: str, arguments: Dict[str, Any], executor_fn, *args,
                             call_id: Optional[str] = None, parent_id: Optional[str] = None,
                             step: Optional[int] = None, server: Optional[str] = None, **kwargs) -> Any:
        start_timestamp = time.time()
        started = time.perf_counter()
        error = None
        result = None
        try:
            result = executor_fn(*args, **kwargs)
            if isinstance(result, dict) and result.get("error"):
                error = str(result.get("error"))
                self.record_error(
                    f"tool:{name}",
                    error,
                    call_id=call_id,
                    parent_id=parent_id,
                    step=step,
                )
            return result
        except Exception as exc:
            error = str(exc)
            self.record_error(
                f"tool:{name}",
                error,
                call_id=call_id,
                parent_id=parent_id,
                step=step,
                exception=exc,
            )
            raise
        finally:
            end_timestamp = time.time()
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            entry = {"name": name, "arguments": self._sanitize_trace_value(arguments),
                     "result": self._sanitize_trace_value(result), "error": error,
                     "timing_ms": elapsed_ms, "timestamp": end_timestamp,
                     "start_timestamp": start_timestamp, "end_timestamp": end_timestamp}
            if call_id is not None: entry["call_id"] = str(call_id)
            if parent_id is not None: entry["parent_id"] = str(parent_id)
            if step is not None: entry["step"] = step
            if server is not None: entry["server"] = server
            self.trace["tool_calls"].append(entry)
            self.add_event("tool_executed", {"name": name, "timing_ms": elapsed_ms,
                                              "success": error is None,
                                              "start_timestamp": start_timestamp,
                                              "end_timestamp": end_timestamp,
                                              **({"call_id": str(call_id)} if call_id is not None else {}),
                                              **({"parent_id": str(parent_id)} if parent_id is not None else {}),
                                              **({"step": step} if step is not None else {})})

    def add_event(self, event_type: str, payload: Optional[Dict[str, Any]] = None) -> None:
        if event_type in self._INTERNAL_EVENT_TYPES:
            return
        self.trace["events"].append({"type": event_type, "timestamp": time.time(), "payload": payload or {}})

    def record_error(self, source: str, message: str, call_id: Optional[str] = None,
                     parent_id: Optional[str] = None, step: Optional[int] = None,
                     error_type: Optional[str] = None, exception: Optional[BaseException] = None) -> None:
        entry = {"source": source, "error": message, "timestamp": time.time()}
        if error_type: entry["type"] = error_type
        if call_id is not None: entry["call_id"] = str(call_id)
        if parent_id is not None: entry["parent_id"] = str(parent_id)
        if step is not None: entry["step"] = step
        if exception is not None: entry["python_exception"] = self._capture_exception_state(exception)
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
            self._finalized = True
        try:
            from billing import aggregate_billing
            billing = self.trace.get("billing") or {}
            self.trace["billing"] = aggregate_billing(billing.get("items", []), self.trace.get("context", {}))
        except Exception as exc:
            self.add_event("billing_error", {"error": str(exc)})

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
            return {"trace_id": self.trace_id, "schema_version": self.SCHEMA_VERSION,
                    "error": "trace_serialization_failed", "timings": self.trace.get("timings", {}),
                    "errors": [str(e) for e in self.trace.get("errors", [])]}
import time
import uuid
import json
from typing import Any, Dict, List, Optional

class ExecutionTrace:
    def __init__(self, trace_id: Optional[str] = None):
        self.trace_id = trace_id or str(uuid.uuid4())
        self.start_perf = time.perf_counter()
        self.trace: Dict[str, Any] = {
            "trace_id": self.trace_id,
            "created_at": time.time(),
            "request": {},
            "responses": [],
            "tool_calls": [],
            "events": [],
            "timings": {},
            "errors": []
        }

    def get_metadata(self) -> Dict[str, str]:
        """Return metadata dict for Yandex API with trace_id (all values as strings)."""
        return {
            "trace_id": str(self.trace_id)
        }

    def set_request(self, payload: Dict[str, Any]) -> None:
        """Store the initial request payload."""
        if isinstance(payload, dict):
            clean_payload = {k: v for k, v in payload.items() if k != "trace"}
        else:
            clean_payload = payload
        self.trace["request"] = clean_payload
        self.add_event("request_initialized", {
            "keys": list(clean_payload.keys()) if isinstance(clean_payload, dict) else []
        })

    def add_response(self, raw_json: Dict[str, Any], step_index: int = 1, **kwargs) -> None:
        """Store raw JSON response from Yandex API (full response, unmodified)."""
        idx = step_index or kwargs.get("call_index", 1)
        # Create isolated copy without trace field to avoid circular reference
        if isinstance(raw_json, dict):
            clean_raw = {k: v for k, v in raw_json.items() if k not in ("trace", "step_timings")}
        else:
            clean_raw = raw_json

        self.trace["responses"].append({
            "step": idx,
            "timestamp": time.time(),
            "raw": clean_raw
        })
        self.add_event("api_response_received", {
            "step": idx,
            "status": raw_json.get("status") if isinstance(raw_json, dict) else None,
            "has_tool_calls": bool(raw_json.get("output")) if isinstance(raw_json, dict) else False
        })

    def track_tool_execution(self, name: str, arguments: Dict[str, Any], executor_fn, *args, **kwargs) -> Any:
        """Track a tool execution with timing and error handling."""
        started = time.perf_counter()
        error = None
        result = None
        try:
            result = executor_fn(*args, **kwargs)
            return result
        except Exception as exc:
            error = str(exc)
            self.record_error(f"tool:{name}", error)
            raise
        finally:
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            entry = {
                "name": name,
                "arguments": arguments,
                "result": result,
                "error": error,
                "timing_ms": elapsed_ms,
                "timestamp": time.time()
            }
            self.trace["tool_calls"].append(entry)
            self.add_event("tool_executed", {
                "name": name,
                "timing_ms": elapsed_ms,
                "success": error is None
            })

    def add_event(self, event_type: str, payload: Optional[Dict[str, Any]] = None) -> None:
        """Add a chronological event to the timeline."""
        self.trace["events"].append({
            "type": event_type,
            "timestamp": time.time(),
            "payload": payload or {}
        })

    def record_error(self, source: str, message: str) -> None:
        """Record an error with source and message."""
        self.trace["errors"].append({
            "source": source,
            "error": message,
            "timestamp": time.time()
        })
        self.add_event("error_occurred", {"source": source, "error": message})

    def finalize(self) -> Dict[str, Any]:
        """Finalize the trace, compute total duration, return immutable copy."""
        total_ms = round((time.perf_counter() - self.start_perf) * 1000, 2)
        self.trace["timings"]["total_duration_ms"] = total_ms
        self.add_event("trace_finalized", {"total_duration_ms": total_ms})
        # Return a deep copy to prevent external mutations
        return json.loads(json.dumps(self.trace))

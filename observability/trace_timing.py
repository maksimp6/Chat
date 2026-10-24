"""Small trace timing/correlation helpers kept outside ExecutionTrace.

The helpers are pure with respect to the trace mapping and keep the public
ExecutionTrace API unchanged.
"""

from typing import Any, Dict, Optional, Tuple


def request_timing_for_step(
    trace: Dict[str, Any], step_index: int
) -> Tuple[Optional[float], Optional[float]]:
    start = None
    api_requests = trace.get("api_requests", [])
    for request in reversed(api_requests):
        if request.get("step") == step_index:
            value = request.get("timestamp")
            if isinstance(value, (int, float)):
                start = float(value)
            break
    for event in reversed(trace.get("events", [])):
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


def infer_response_start(
    trace: Dict[str, Any], step_index: int, end_timestamp: float
) -> Optional[float]:
    request_start, _ = request_timing_for_step(trace, step_index)
    if request_start is not None:
        return request_start
    if trace.get("responses"):
        previous = trace["responses"][-1]
        previous_end = previous.get("end_timestamp", previous.get("timestamp"))
        if isinstance(previous_end, (int, float)):
            return float(previous_end)
    created = trace.get("created_at")
    if isinstance(created, (int, float)):
        return float(created)
    return None


def step_correlation_id(trace_id: str, step_index: int) -> str:
    return f"{trace_id}:step:{int(step_index)}"

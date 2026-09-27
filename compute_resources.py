"""Computation resource tracking and billing for ExecutionTrace.

Tracks CPU time, tool execution, API processing, and other computational
resources used during invocation. Costs are derived from execution timing
and configured pricing models, independent of AI token usage.
"""

from typing import Any, Dict, Optional

PRICING_CURRENCY = "RUB"
PRICING_VERSION = "compute-v1"
PROVIDER = "alice_compute"

COMPUTE_PRICING = {
    "cpu_time_per_minute": 0.05,
    "tool_execution_per_minute": 0.10,
    "api_call_per_second": 0.02,
}


def extract_timing_metrics(trace: Dict[str, Any]) -> Dict[str, float]:
    """Extract timing metrics from an ExecutionTrace."""
    metrics = {
        "total_duration_ms": 0.0,
        "api_time_ms": 0.0,
        "tool_time_ms": 0.0,
        "processing_time_ms": 0.0,
    }

    timings = trace.get("timings", {})
    metrics["total_duration_ms"] = float(timings.get("total_duration_ms", 0))

    for response in trace.get("responses", []):
        if isinstance(response.get("timing_ms"), (int, float)):
            metrics["api_time_ms"] += float(response["timing_ms"])

    for tool_call in trace.get("tool_calls", []):
        if isinstance(tool_call.get("timing_ms"), (int, float)):
            metrics["tool_time_ms"] += float(tool_call["timing_ms"])

    metrics["processing_time_ms"] = max(
        0.0, metrics["total_duration_ms"] - metrics["api_time_ms"]
    )

    return metrics


def calculate_compute_cost(metrics: Dict[str, float]) -> Dict[str, Any]:
    """Calculate compute costs based on timing metrics."""
    cpu_time_min = metrics.get("processing_time_ms", 0) / 60000
    tool_time_min = metrics.get("tool_time_ms", 0) / 60000
    api_time_sec = metrics.get("api_time_ms", 0) / 1000
    api_calls = len(metrics.get("api_calls", []))
    api_call_cost = (
        api_calls * COMPUTE_PRICING["api_call_per_second"]
        if api_calls > 0
        else api_time_sec * COMPUTE_PRICING["api_call_per_second"]
    )

    cpu_cost = cpu_time_min * COMPUTE_PRICING["cpu_time_per_minute"]
    tool_cost = tool_time_min * COMPUTE_PRICING["tool_execution_per_minute"]
    total_cost = cpu_cost + tool_cost + api_call_cost

    return {
        "cpu_time_ms": metrics.get("processing_time_ms", 0),
        "cpu_cost": round(cpu_cost, 6),
        "tool_time_ms": metrics.get("tool_time_ms", 0),
        "tool_cost": round(tool_cost, 6),
        "api_time_ms": metrics.get("api_time_ms", 0),
        "api_call_cost": round(api_call_cost, 6),
        "total_compute_cost": round(total_cost, 6),
    }


def build_compute_billing_item(
    trace: Dict[str, Any],
    step: int = 1,
) -> Dict[str, Any]:
    """Build a compute resource billing item from an ExecutionTrace."""
    metrics = extract_timing_metrics(trace)
    compute_cost = calculate_compute_cost(metrics)

    return {
        "type": "compute",
        "step": step,
        "provider": PROVIDER,
        "currency": PRICING_CURRENCY,
        "pricing_version": PRICING_VERSION,
        "cost_status": "calculated",
        **compute_cost,
        "total_cost": compute_cost["total_compute_cost"],
    }


def aggregate_compute_costs(items: list, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Aggregate compute cost items into a summary."""
    compute_items = [item for item in items if item.get("type") == "compute"]

    if not compute_items:
        return {
            "currency": PRICING_CURRENCY,
            "provider": PROVIDER,
            "pricing_version": PRICING_VERSION,
            "cpu_cost": 0.0,
            "tool_cost": 0.0,
            "api_call_cost": 0.0,
            "total_cost": 0.0,
            "items": [],
            "cost_status": "calculated",
        }

    total_cost = round(sum(float(item.get("total_cost", 0)) for item in compute_items), 6)
    cpu_cost = round(sum(float(item.get("cpu_cost", 0)) for item in compute_items), 6)
    tool_cost = round(sum(float(item.get("tool_cost", 0)) for item in compute_items), 6)
    api_call_cost = round(sum(float(item.get("api_call_cost", 0)) for item in compute_items), 6)
    total_cpu_time = sum(float(item.get("cpu_time_ms", 0)) for item in compute_items)
    total_tool_time = sum(float(item.get("tool_time_ms", 0)) for item in compute_items)
    total_api_time = sum(float(item.get("api_time_ms", 0)) for item in compute_items)

    result = {
        "currency": PRICING_CURRENCY,
        "provider": PROVIDER,
        "pricing_version": PRICING_VERSION,
        "cpu_time_ms": total_cpu_time,
        "cpu_cost": cpu_cost,
        "tool_time_ms": total_tool_time,
        "tool_cost": tool_cost,
        "api_time_ms": total_api_time,
        "api_call_cost": api_call_cost,
        "total_cost": total_cost,
        "cost_status": "calculated",
        "items": compute_items,
    }
    if context:
        for key in ("invocation_id", "session_id", "conversation_id", "trace_id", "owner_id"):
            if context.get(key) is not None:
                result[key] = str(context[key])
    return result

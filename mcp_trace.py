"""Trace correlation helpers for MCP activity managed by Yandex AI Studio."""
from typing import Any, Dict, Iterable, List


_MCP_TYPE_MARKERS = ("mcp",)
_MCP_KEY_MARKERS = ("mcp_call", "mcp_server", "mcp_tool", "mcp_list", "mcp_approval")


def _looks_like_mcp_item(value: Dict[str, Any]) -> bool:
    item_type = str(value.get("type", "")).lower()
    if item_type.startswith(_MCP_TYPE_MARKERS) or "_mcp_" in item_type:
        return True
    keys = {str(key).lower() for key in value}
    return any(marker in key for key in keys for marker in _MCP_KEY_MARKERS)


def _walk_mcp_items(value: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(value, dict):
        if _looks_like_mcp_item(value):
            yield value
        for child in value.values():
            yield from _walk_mcp_items(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_mcp_items(child)


def _activity_key(item: Dict[str, Any], response_id: Any, step: Any) -> str:
    call_id = item.get("call_id") or item.get("id") or item.get("tool_call_id")
    name = item.get("name") or item.get("tool_name")
    server = item.get("server_label") or item.get("server_name") or item.get("server_url")
    return "|".join(str(value or "") for value in (step, response_id, call_id, server, name, item.get("type")))


def record_yandex_mcp_activity(trace: Any) -> int:
    """Extract MCP activity exposed by Yandex Responses snapshots into the trace.

    This observes the Responses payload only. Alice Pro never connects to or
    executes the external MCP server itself.
    """
    if trace is None or not hasattr(trace, "trace"):
        return 0

    responses = trace.trace.get("responses", [])
    recorded = trace.trace.setdefault("mcp_activity", [])
    seen = {entry.get("activity_key") for entry in recorded if isinstance(entry, dict)}
    count = 0

    for response_entry in responses:
        if not isinstance(response_entry, dict):
            continue
        step = response_entry.get("step")
        response_id = response_entry.get("response_id")
        snapshots: List[Any] = []
        raw = response_entry.get("raw")
        if raw is not None:
            snapshots.append(raw)
        snapshots.extend(response_entry.get("poll_snapshots") or [])

        for snapshot in snapshots:
            for item in _walk_mcp_items(snapshot):
                key = _activity_key(item, response_id, step)
                if key in seen:
                    continue
                seen.add(key)
                clean = trace._sanitize_trace_value(item)
                entry = {
                    "activity_key": key,
                    "step": step,
                    "response_id": response_id,
                    "type": item.get("type"),
                    "data": clean,
                }
                recorded.append(entry)
                trace.add_event("mcp_activity_observed", {
                    "step": step,
                    "response_id": response_id,
                    "type": item.get("type"),
                    "call_id": item.get("call_id") or item.get("id") or item.get("tool_call_id"),
                    "name": item.get("name") or item.get("tool_name"),
                    "server": item.get("server_label") or item.get("server_name"),
                })
                count += 1

    return count

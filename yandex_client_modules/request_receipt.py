"""Content fingerprints of an assembled request, without its sensitive text.

These are canonical semantic JSON fingerprints, not hashes of Requests' wire
serialization and not evidence of provider-effective settings or acceptance.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any

CANONICALIZATION = "python-json-sorted-utf8-compact-v1"
_MODEL_URI = re.compile(r"gpt://[A-Za-z0-9_.-]{1,128}/[A-Za-z0-9_.:-]{1,128}/[A-Za-z0-9_.-]{1,64}")
_CREDENTIAL_MARKER = re.compile(r"gh[pousr]_|github_pat_|sk-|AQVN|-----BEGIN", re.IGNORECASE)
_BOOLEANS = ("background", "store", "stream", "parallel_tool_calls")
_NUMBERS = ("temperature", "top_p", "max_output_tokens", "max_tool_calls")
_ENUMS = {
    "truncation": {"auto", "disabled"},
    "service_tier": {"auto", "default", "flex", "priority"},
    "tool_choice": {"auto", "none", "required"},
}
_EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh", "max"}
_INPUT_TYPES = {"message", "function_call", "function_call_output", "reasoning"}
_TOOL_TYPES = {"function", "mcp", "file_search", "web_search", "code_interpreter"}


def _fingerprint(value: Any) -> dict[str, Any]:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return {
        "canonical_sha256": hashlib.sha256(encoded).hexdigest(),
        "canonical_bytes": len(encoded),
    }


def _field(payload: dict[str, Any], name: str) -> dict[str, Any]:
    if name not in payload:
        return {"state": "absent"}
    return {"state": "present", **_fingerprint(payload[name])}


def _outgoing_settings(payload: dict[str, Any]) -> dict[str, Any]:
    settings = {name: _field(payload, name) for name in (*_BOOLEANS, *_NUMBERS, *_ENUMS)}
    for name in _BOOLEANS:
        if type(payload.get(name)) is bool:
            settings[name]["value"] = payload[name]
    for name in _NUMBERS:
        value = payload.get(name)
        if type(value) is int or isinstance(value, float) and math.isfinite(value):
            settings[name]["value"] = value
    for name, allowed in _ENUMS.items():
        value = payload.get(name)
        if isinstance(value, str) and value in allowed:
            settings[name]["value"] = value
    settings["reasoning"] = _field(payload, "reasoning")
    reasoning = payload.get("reasoning")
    if isinstance(reasoning, dict):
        effort = reasoning.get("effort")
        if isinstance(effort, str) and effort in _EFFORTS:
            settings["reasoning"]["effort"] = effort
    return settings


def _input_manifest(payload: dict[str, Any]) -> dict[str, Any]:
    result = _field(payload, "input")
    items = payload.get("input")
    if not isinstance(items, list):
        return result
    result["item_count"] = len(items)
    counts = dict.fromkeys(sorted(_INPUT_TYPES | {"other"}), 0)
    call_links = []
    for item in items:
        kind = (
            item.get("type", "message" if "role" in item else "other")
            if isinstance(item, dict)
            else "other"
        )
        kind = kind if isinstance(kind, str) and kind in _INPUT_TYPES else "other"
        counts[kind] += 1
        if kind in {"function_call", "function_call_output"} and "call_id" in item:
            call_links.append({"type": kind, "call_id": item["call_id"]})
    result["item_types"] = counts
    result["call_linkage"] = {"count": len(call_links), **_fingerprint(call_links)}
    return result


def _tools_manifest(payload: dict[str, Any]) -> dict[str, Any]:
    result = _field(payload, "tools")
    tools = payload.get("tools")
    if not isinstance(tools, list):
        return result
    counts = dict.fromkeys(sorted(_TOOL_TYPES | {"other"}), 0)
    for tool in tools:
        kind = tool.get("type") if isinstance(tool, dict) else None
        kind = kind if isinstance(kind, str) and kind in _TOOL_TYPES else "other"
        counts[kind] += 1
    result.update(count=len(tools), types=counts)
    return result


def build_request_manifest(payload: dict[str, Any]) -> dict[str, Any]:
    """Describe only fields in the final payload; presence is not delivery proof."""
    model = _field(payload, "model")
    model_value = payload.get("model")
    if (
        isinstance(model_value, str)
        and _MODEL_URI.fullmatch(model_value)
        and not _CREDENTIAL_MARKER.search(model_value)
    ):
        model["value"] = model_value
    return {
        "schema_version": 1,
        "capture_stage": "assembled_payload_before_post",
        "canonicalization": CANONICALIZATION,
        "wire_bytes_verified": False,
        "provider_effective": {"state": "unknown"},
        "payload": _fingerprint(payload),
        "model": model,
        "settings": _outgoing_settings(payload),
        "input": _input_manifest(payload),
        "tools": _tools_manifest(payload),
        "instructions": _field(payload, "instructions"),
        "prompt": _field(payload, "prompt"),
        "text": _field(payload, "text"),
        "metadata": _field(payload, "metadata"),
        "linkage": {
            name: _field(payload, name)
            for name in ("conversation", "previous_response_id", "prompt_cache_key")
        },
    }

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
# Provider model URIs may include an explicit version segment.
_MODEL_URI = re.compile(
    r"gpt://[A-Za-z0-9_.-]{1,128}/[A-Za-z0-9_.:-]{1,128}(?:/[A-Za-z0-9_.-]{1,64})?"
)
_CREDENTIAL_MARKER = re.compile(
    r"gh[pousr]_|github_pat_|sk-|AQVN|-----BEGIN", re.IGNORECASE
)
_BOOLEANS = ("background", "store", "stream", "parallel_tool_calls")
_NUMBERS = (
    "temperature",
    "top_p",
    "top_logprobs",
    "max_output_tokens",
    "max_tool_calls",
)
_ENUMS = {
    "truncation": {"auto", "disabled"},
    "service_tier": {"auto", "default", "flex", "scale", "priority"},
    "tool_choice": {"auto", "none", "required"},
}
_EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh"}
_INPUT_TYPES = {"message", "function_call", "function_call_output", "reasoning"}
_CONTENT_TYPES = {
    "input_text",
    "input_image",
    "input_audio",
    "input_file",
    "output_text",
}
_TOOL_TYPES = {"function", "mcp", "file_search", "web_search", "code_interpreter"}
_FUNCTION_NAME = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")
_FUNCTION_NAME_CREDENTIAL = re.compile(
    r"gh[pousr]_|github_pat_|sk-|AQVN|BEGIN", re.IGNORECASE
)
# Both dimensions stay below the generic trace sanitizer's 50-item boundary.
# This bounds receipt metadata only; it never changes the provider's tool list.
_FUNCTION_NAMES_PER_BATCH = 40
_FUNCTION_NAME_BATCHES = 50


def _fingerprint(value: Any) -> dict[str, Any]:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
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
    settings = {
        name: _field(payload, name) for name in (*_BOOLEANS, *_NUMBERS, *_ENUMS)
    }
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
    content_counts = dict.fromkeys(sorted(_CONTENT_TYPES | {"other"}), 0)
    call_links = []
    for item in items:
        kind = (
            item.get("type", "message" if "role" in item else "other")
            if isinstance(item, dict)
            else "other"
        )
        kind = kind if isinstance(kind, str) and kind in _INPUT_TYPES else "other"
        counts[kind] += 1
        if kind == "message":
            content = item.get("content")
            if isinstance(content, str):
                content_counts[
                    "output_text" if item.get("role") == "assistant" else "input_text"
                ] += 1
            elif isinstance(content, list):
                for part in content:
                    content_type = part.get("type") if isinstance(part, dict) else None
                    content_type = (
                        content_type
                        if isinstance(content_type, str)
                        and content_type in _CONTENT_TYPES
                        else "other"
                    )
                    content_counts[content_type] += 1
        if kind in {"function_call", "function_call_output"} and "call_id" in item:
            call_links.append({"type": kind, "call_id": item["call_id"]})
    result["item_types"] = counts
    # Counts identify photos/audio without recording their URLs or binary data.
    result["content_types"] = content_counts
    result["call_linkage"] = {"count": len(call_links), **_fingerprint(call_links)}
    return result


def _tools_manifest(payload: dict[str, Any]) -> dict[str, Any]:
    result = _field(payload, "tools")
    tools = payload.get("tools")
    if not isinstance(tools, list):
        return result
    counts = dict.fromkeys(sorted(_TOOL_TYPES | {"other"}), 0)
    names = []
    names_complete = True
    for tool in tools:
        kind = tool.get("type") if isinstance(tool, dict) else None
        kind = kind if isinstance(kind, str) and kind in _TOOL_TYPES else "other"
        counts[kind] += 1
        if kind == "other":
            names_complete = False
        if kind != "function":
            continue
        function = tool.get("function")
        name = tool.get("name") or (
            function.get("name") if isinstance(function, dict) else None
        )
        if (
            not isinstance(name, str)
            or not _FUNCTION_NAME.fullmatch(name)
            or _FUNCTION_NAME_CREDENTIAL.search(name)
        ):
            names_complete = False
            continue
        if len(names) >= _FUNCTION_NAMES_PER_BATCH * _FUNCTION_NAME_BATCHES:
            names_complete = False
            continue
        names.append(name)
    result.update(
        count=len(tools),
        types=counts,
        function_name_count=counts["function"],
        function_names_complete=names_complete,
        function_name_batches=[
            names[index : index + _FUNCTION_NAMES_PER_BATCH]
            for index in range(0, len(names), _FUNCTION_NAMES_PER_BATCH)
        ],
    )
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


# One trace string holds at most 4000 characters, so long texts travel in parts.
_VIEW_PART = 3900
_VIEW_ITEMS = 48


def _view_parts(text: str, limit: int = _VIEW_ITEMS) -> list[str]:
    return [text[i : i + _VIEW_PART] for i in range(0, len(text), _VIEW_PART)][:limit]


def _view_chars(value: Any) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False, sort_keys=True))
    except (TypeError, ValueError):
        return len(str(value))


def _view_tool(tool: dict[str, Any]) -> tuple[str, str]:
    """(group, name): function tools are grouped by the first word of their name."""
    kind = str(tool.get("type") or "function")
    if kind != "function":
        return kind, str(tool.get("server_label") or tool.get("name") or kind)
    function = tool.get("function") if isinstance(tool.get("function"), dict) else {}
    name = str(tool.get("name") or function.get("name") or "?")
    if name.startswith("tools_"):
        # A whole category sent as one function (tool_groups): its own row.
        return name[len("tools_"):] or name, name
    return re.split(r"[._]", name, maxsplit=1)[0] or name, name


def _view_message_text(item: dict[str, Any]) -> str:
    content = item.get("content")
    if isinstance(content, str):
        return content
    texts = []
    for part in content if isinstance(content, list) else ():
        if isinstance(part, dict) and isinstance(part.get("text"), str):
            texts.append(part["text"])
        elif isinstance(part, dict):
            texts.append(f"[{part.get('type', 'part')}]")
    return "\n".join(texts)


def build_model_view(payload: dict[str, Any]) -> dict[str, Any]:
    """What this request shows the model, as readable text and sizes.

    Instructions and message texts are kept whole, split into trace-sized parts.
    Tool schemas are not copied, only each tool's name and size; tool outputs
    only their size. The provider adds its stored conversation on top of this.
    """
    instructions = payload.get("instructions")
    instructions = instructions if isinstance(instructions, str) else ""
    tools = payload.get("tools") if isinstance(payload.get("tools"), list) else []
    groups: dict[str, dict[str, Any]] = {}
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        group, name = _view_tool(tool)
        entry = groups.setdefault(
            group, {"group": group, "count": 0, "chars": 0, "deferred": 0, "sized": []}
        )
        size = _view_chars(tool)
        entry["count"] += 1
        entry["chars"] += size
        # A deferred tool is not shown to the model until its tool search finds it.
        entry["deferred"] += 1 if tool.get("defer_loading") is True else 0
        entry["sized"].append((size, name))
    tool_groups = []
    for entry in sorted(groups.values(), key=lambda item: -item["chars"]):
        sized = sorted(entry.pop("sized"), reverse=True)
        entry["names"] = ", ".join(f"{name} {size}" for size, name in sized)[:_VIEW_PART]
        tool_groups.append(entry)

    raw_input = payload.get("input")
    if isinstance(raw_input, str):
        items: list[Any] = [{"type": "message", "role": "user", "content": raw_input}]
    else:
        items = raw_input if isinstance(raw_input, list) else []
    input_rows = []
    for item in items[:_VIEW_ITEMS]:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type") or "message")
        row: dict[str, Any] = {"type": kind, "chars": _view_chars(item)}
        if isinstance(item.get("role"), str):
            row["role"] = item["role"]
        if kind == "message":
            row["text_parts"] = _view_parts(_view_message_text(item), 10)
        elif kind == "function_call":
            if isinstance(item.get("name"), str):
                row["name"] = item["name"]
            row["text_parts"] = _view_parts(str(item.get("arguments") or ""), 10)
        elif kind == "function_call_output":
            output = item.get("output")
            row["text_parts"] = _view_parts(
                output if isinstance(output, str) else json.dumps(output, ensure_ascii=False), 10
            )
        input_rows.append(row)
    if len(items) > _VIEW_ITEMS:
        input_rows.append({"type": "omitted", "count": len(items) - _VIEW_ITEMS})

    choice = payload.get("tool_choice")
    model = payload.get("model")
    return {
        "model": model
        if isinstance(model, str) and not _CREDENTIAL_MARKER.search(model)
        else None,
        "tool_choice": choice if isinstance(choice, str) or choice is None else json.dumps(choice),
        "payload_chars": _view_chars(payload),
        "instructions": {"chars": len(instructions), "text_parts": _view_parts(instructions)},
        "tools": {
            "count": len(tools),
            "chars": _view_chars(tools) if tools else 0,
            "deferred": sum(entry["deferred"] for entry in groups.values()),
            "groups": tool_groups[:_VIEW_ITEMS],
        },
        "input": {
            "count": len(items),
            "chars": _view_chars(raw_input) if raw_input is not None else 0,
            "items": input_rows,
        },
        "history": {
            "provider_conversation": bool(payload.get("conversation")),
            "previous_response": bool(payload.get("previous_response_id")),
            "truncation": payload.get("truncation")
            if isinstance(payload.get("truncation"), str)
            else None,
        },
    }

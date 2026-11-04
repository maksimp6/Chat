"""One chat request to Ollama's /api/chat, with Responses API input and output.

Ollama keeps no conversation: the caller sends the whole dialog every time.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

import requests

__all__ = ["OllamaError", "ask", "chat_messages", "chat_tools"]

_TEXT_PARTS = ("input_text", "output_text", "text")


class OllamaError(Exception):
    """Ollama did not answer: ``reached`` is False when the request never got there."""

    def __init__(self, message: str, *, status_code: int | None = None, reached: bool = True):
        super().__init__(message)
        self.status_code = status_code
        self.reached = reached


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    parts = []
    for part in content if isinstance(content, list) else []:
        if isinstance(part, str):
            parts.append(part)
        elif isinstance(part, dict) and part.get("type") in _TEXT_PARTS:
            parts.append(str(part.get("text") or ""))
    return "\n".join(part for part in parts if part)


def chat_messages(instructions: Any, items: Any) -> list[dict[str, Any]]:
    """Responses input (a string or items) as Ollama chat messages."""
    if isinstance(items, str):
        items = [{"role": "user", "content": items}]
    messages: list[dict[str, Any]] = []
    if isinstance(instructions, str) and instructions.strip():
        messages.append({"role": "system", "content": instructions})
    names: dict[Any, Any] = {}
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        if kind == "function_call":
            try:
                arguments = json.loads(item.get("arguments") or "{}")
            except (TypeError, ValueError):
                arguments = {}
            names[item.get("call_id")] = item.get("name")
            call = {
                "function": {
                    "name": item.get("name"),
                    "arguments": arguments if isinstance(arguments, dict) else {},
                }
            }
            last = messages[-1] if messages else None
            if last is not None and last["role"] == "assistant":
                last.setdefault("tool_calls", []).append(call)
            else:
                messages.append({"role": "assistant", "content": "", "tool_calls": [call]})
        elif kind == "function_call_output":
            output = item.get("output")
            messages.append(
                {
                    "role": "tool",
                    "content": output
                    if isinstance(output, str)
                    else json.dumps(output, ensure_ascii=False, default=str),
                    "tool_name": names.get(item.get("call_id")) or "",
                }
            )
        elif kind == "reasoning":
            continue
        elif "role" in item:
            role = "system" if item["role"] == "developer" else item["role"]
            if role in ("system", "user", "assistant"):
                messages.append({"role": role, "content": _text(item.get("content"))})
    return messages


def chat_tools(tools: Any) -> list[dict[str, Any]]:
    """The function tools; hosted provider tools (web and file search, MCP) do not exist locally."""
    result = []
    for tool in tools if isinstance(tools, list) else []:
        if isinstance(tool, dict) and tool.get("type") == "function" and tool.get("name"):
            result.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool.get("description") or "",
                        "parameters": tool.get("parameters")
                        or {"type": "object", "properties": {}},
                    },
                }
            )
    return result


def _response(data: dict[str, Any], model_key: str, started: float) -> dict[str, Any]:
    message = data.get("message") if isinstance(data.get("message"), dict) else {}
    output: list[dict[str, Any]] = []
    if message.get("thinking"):
        output.append(
            {
                "type": "reasoning",
                "id": "rs_" + uuid.uuid4().hex,
                "summary": [{"type": "summary_text", "text": message["thinking"]}],
            }
        )
    if message.get("content"):
        output.append(
            {
                "type": "message",
                "id": "msg_" + uuid.uuid4().hex,
                "role": "assistant",
                "status": "completed",
                "content": [
                    {"type": "output_text", "text": message["content"], "annotations": []}
                ],
            }
        )
    for call in message.get("tool_calls") or []:
        function = call.get("function") if isinstance(call, dict) else None
        if not isinstance(function, dict) or not function.get("name"):
            continue
        arguments = function.get("arguments")
        output.append(
            {
                "type": "function_call",
                "id": "fc_" + uuid.uuid4().hex,
                "call_id": call.get("id") or "call_" + uuid.uuid4().hex,
                "name": function["name"],
                "arguments": arguments
                if isinstance(arguments, str)
                else json.dumps(arguments or {}, ensure_ascii=False),
                "status": "completed",
            }
        )
    input_tokens = int(data.get("prompt_eval_count") or 0)
    output_tokens = int(data.get("eval_count") or 0)
    cut = data.get("done_reason") == "length"
    result = {
        "id": "ollama_" + uuid.uuid4().hex,
        "object": "response",
        "created_at": int(started),
        "completed_at": int(time.time()),
        "status": "incomplete" if cut else "completed",
        "model": model_key,
        "output": output,
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 0},
        },
    }
    if cut:
        result["incomplete_details"] = {"reason": "max_output_tokens"}
    return result


def build_body(upstream: str, items: Any, params: dict[str, Any], context: int) -> dict[str, Any]:
    """The /api/chat body for Responses-style ``params`` (instructions, tools, text format)."""
    options: dict[str, Any] = {"num_ctx": int(context)}
    if params.get("temperature") is not None:
        options["temperature"] = float(params["temperature"])
    if params.get("top_p") is not None:
        options["top_p"] = float(params["top_p"])
    if params.get("max_output_tokens"):
        options["num_predict"] = int(params["max_output_tokens"])
    body: dict[str, Any] = {
        "model": upstream,
        "messages": chat_messages(params.get("instructions"), items),
        "stream": False,
        "think": True,
        "options": options,
    }
    tools = chat_tools(params.get("tools"))
    if tools:
        body["tools"] = tools
    text = params.get("text")
    text_format = text.get("format") if isinstance(text, dict) else None
    if isinstance(text_format, dict) and text_format.get("type") == "json_schema":
        body["format"] = text_format.get("schema") or "json"
    return body


def ask(
    url: str,
    upstream: str,
    model_key: str,
    items: Any,
    params: dict[str, Any],
    *,
    context: int,
    timeout: float | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """(request body, Responses-shaped answer) of one /api/chat request to Ollama at ``url``."""
    body = build_body(upstream, items, params, context)
    endpoint = url.rstrip("/") + "/api/chat"
    started = time.time()
    try:
        resp = requests.post(endpoint, json=body, timeout=timeout)
    except requests.RequestException as exc:
        raise OllamaError(
            f"Ollama {endpoint} unreachable: {type(exc).__name__}: {exc}", reached=False
        ) from exc
    if resp.status_code != 200:
        raise OllamaError(
            f"Ollama HTTP {resp.status_code}: {resp.text[:500]}", status_code=resp.status_code
        )
    try:
        data = resp.json()
    except ValueError as exc:
        raise OllamaError("Ollama returned a response that is not valid JSON", status_code=200) from exc
    return body, _response(data if isinstance(data, dict) else {}, model_key, started)

"""One turn through asmrun's own API, POST /v1/turn (the runner's replacement for
Ollama's /api/chat; owner, 08.11.2026: "replace olama api with better one").

The runner keeps one context from boot, so a turn carries only what is new: the
latest user message, or the results of the tool calls it asked for. The system text
and the tools go along every time and are used when the runner's state is fresh.
The answer streams back as NDJSON: {"delta": text} per generated piece, then one
{"done": true, "content", "tool_calls", "stop", "prompt_tokens", "gen_tokens",
"prompt_ms", "gen_ms", "mhz", "temp_c", "pkg_temp_c", "ctx_used", "ctx"} line with
the turn's numbers and the cores' temperatures (null under Windows, where the
runner cannot read the sensors). The answer comes back in the Responses shape, as
chat.ask gives it, with the hardware numbers under "asmrun".
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable
from typing import Any

import requests

from ollama_client.chat import OllamaError, _text, chat_tools

__all__ = ["ask", "turn_body"]

_TOOL_CALL_MARK = "<tool_call>"


def _first_sentence(text: Any, limit: int = 160) -> str:
    text = " ".join(str(text or "").split())
    for stop in (". ", "\n"):
        cut = text.find(stop)
        if 0 < cut < limit:
            return text[: cut + 1].strip()
    return text[:limit].strip()


def compact_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The category functions with their action list as one line each, no JSON
    schemas: the registry's full form (every action's parameter schema under
    anyOf, Russian descriptions) is ~21,000 tokens for filesystem and git alone,
    which the local model reads at a few tokens a second and cannot keep in its
    attention window. Here: name, a one-sentence description, the actions as
    ``name(param, optional?) — what it does``, and parameters {tool, arguments}."""
    result: list[dict[str, Any]] = []
    for tool in tools:
        function = tool.get("function") if isinstance(tool, dict) else None
        if not isinstance(function, dict) or not function.get("name"):
            continue
        params = function.get("parameters") if isinstance(function.get("parameters"), dict) else {}
        props = params.get("properties") if isinstance(params.get("properties"), dict) else {}
        actions: list[tuple[str, list[str], str]] = []
        arguments = props.get("arguments") if isinstance(props.get("arguments"), dict) else {}
        for option in arguments.get("anyOf") or []:
            if not isinstance(option, dict) or not option.get("title"):
                continue
            required = set(option.get("required") or [])
            names = []
            for key in (option.get("properties") or {}):
                names.append(key if key in required else key + "?")
            actions.append((str(option["title"]), names, _first_sentence(option.get("description"), 90)))
        enum = []
        tool_prop = props.get("tool") if isinstance(props.get("tool"), dict) else {}
        if isinstance(tool_prop.get("enum"), list):
            enum = [str(value) for value in tool_prop["enum"]]
        if not enum:
            enum = [name for name, _names, _desc in actions]
        lines = [_first_sentence(function.get("description"))]
        if actions:
            lines.append("Действия (tool) и параметры (arguments):")
            for name, names, desc in actions:
                line = f"- {name}({', '.join(names)})"
                if desc and not desc.startswith("Параметры действия"):
                    line += " — " + desc
                lines.append(line)
        result.append(
            {
                "type": "function",
                "function": {
                    "name": function["name"],
                    "description": "\n".join(lines),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "tool": {"type": "string", "enum": enum},
                            "arguments": {"type": "object"},
                        },
                        "required": ["tool", "arguments"],
                    },
                },
            }
        )
    return result


def turn_body(instructions: Any, items: Any, tools: Any) -> dict[str, Any]:
    """The /v1/turn body for Responses input: the trailing tool outputs as
    tool_results, else the last user message; the system text and the tools always."""
    if isinstance(items, str):
        items = [{"role": "user", "content": items}]
    items = [item for item in (items if isinstance(items, list) else []) if isinstance(item, dict)]
    system_parts: list[str] = []
    if isinstance(instructions, str) and instructions.strip():
        system_parts.append(instructions)
    names: dict[Any, Any] = {}
    for item in items:
        if item.get("type") == "function_call":
            names[item.get("call_id")] = item.get("name")
        elif item.get("role") in ("system", "developer"):
            text = _text(item.get("content"))
            if text:
                system_parts.append(text)
    body: dict[str, Any] = {}
    if system_parts:
        body["system"] = "\n\n".join(system_parts)
    function_tools = compact_tools(chat_tools(tools))
    if function_tools:
        body["tools"] = function_tools
    # the trailing tool outputs (after the model's calls) are this turn
    results: list[dict[str, Any]] = []
    index = len(items)
    while index > 0 and items[index - 1].get("type") == "function_call_output":
        index -= 1
        item = items[index]
        output = item.get("output")
        results.insert(
            0,
            {
                "name": names.get(item.get("call_id")) or "",
                "content": output if isinstance(output, str) else json.dumps(output, ensure_ascii=False, default=str),
            },
        )
    if results:
        body["tool_results"] = results
        return body
    for item in reversed(items):
        if item.get("role") == "user":
            body["message"] = _text(item.get("content"))
            return body
    body["message"] = ""
    return body


def _response(final: dict[str, Any], model_key: str, started: float) -> dict[str, Any]:
    output: list[dict[str, Any]] = []
    content = final.get("content")
    if isinstance(content, str) and content:
        output.append(
            {
                "type": "message",
                "id": "msg_" + uuid.uuid4().hex,
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": content, "annotations": []}],
            }
        )
    for call in final.get("tool_calls") or []:
        function = call.get("function") if isinstance(call, dict) else None
        if not isinstance(function, dict) or not function.get("name"):
            continue
        arguments = function.get("arguments")
        output.append(
            {
                "type": "function_call",
                "id": "fc_" + uuid.uuid4().hex,
                "call_id": "call_" + uuid.uuid4().hex,
                "name": function["name"],
                "arguments": arguments if isinstance(arguments, str) else json.dumps(arguments or {}, ensure_ascii=False),
                "status": "completed",
            }
        )
    input_tokens = int(final.get("prompt_tokens") or 0)
    output_tokens = int(final.get("gen_tokens") or 0)
    cut = final.get("stop") == "length"
    result = {
        "id": "asmrun_" + uuid.uuid4().hex,
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
        # the runner's own numbers for this turn: shown with the answer and kept in the trace
        "asmrun": {
            key: final.get(key)
            for key in ("prompt_ms", "gen_ms", "mhz", "temp_c", "pkg_temp_c", "ctx_used", "ctx")
        },
    }
    if cut:
        result["incomplete_details"] = {"reason": "max_output_tokens"}
    return result


def ask(
    url: str,
    upstream: str,
    model_key: str,
    items: Any,
    params: dict[str, Any],
    *,
    timeout: float | None,
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """(request body, Responses-shaped answer) of one /v1/turn request to asmrun at ``url``.
    ``upstream`` is not sent: the runner serves the one model it was started with.
    With ``on_event`` every streamed piece of text is reported as {"kind": "text_delta",
    "text"} until a tool call begins (its markup is not shown), and every tool call
    of the final line as {"kind": "tool_call_requested", "name", "arguments"}.
    ``timeout`` bounds the wait between pieces."""
    body = turn_body(params.get("instructions"), items, params.get("tools"))
    endpoint = url.rstrip("/") + "/v1/turn"
    started = time.time()
    try:
        resp = requests.post(endpoint, json=body, timeout=timeout, stream=True)
    except requests.RequestException as exc:
        raise OllamaError(f"asmrun {endpoint} unreachable: {type(exc).__name__}: {exc}", reached=False) from exc
    if resp.status_code != 200:
        raise OllamaError(f"asmrun HTTP {resp.status_code}: {resp.text[:500]}", status_code=resp.status_code)
    report = on_event if on_event is not None else (lambda _event: None)
    final: dict[str, Any] = {}
    pending = ""  # text held back while it may be the start of <tool_call>
    silent = False
    try:
        for line in resp.iter_lines():
            if not line:
                continue
            try:
                chunk = json.loads(line)
            except ValueError as exc:
                raise OllamaError("asmrun streamed a line that is not valid JSON", status_code=200) from exc
            if not isinstance(chunk, dict):
                continue
            if chunk.get("error"):
                raise OllamaError(f"asmrun: {chunk['error']}", status_code=200)
            if chunk.get("done"):
                final = chunk
                continue
            delta = chunk.get("delta")
            if not isinstance(delta, str) or not delta or silent:
                continue
            # the model spells the marker in pieces ("<", "tool", "_call", ">"): a piece
            # that may begin it waits until the next pieces decide
            pending += delta
            if pending.startswith(_TOOL_CALL_MARK):
                silent = True  # the call's markup is not for the person; the final line carries the call
                continue
            if _TOOL_CALL_MARK.startswith(pending):
                continue
            cut = pending.rfind("<")
            if cut > 0 and _TOOL_CALL_MARK.startswith(pending[cut:]):
                report({"kind": "text_delta", "text": pending[:cut]})
                pending = pending[cut:]
                continue
            report({"kind": "text_delta", "text": pending})
            pending = ""
    except requests.RequestException as exc:
        raise OllamaError(f"asmrun stream broke: {type(exc).__name__}: {exc}", status_code=200) from exc
    finally:
        resp.close()
    if pending and not silent:
        report({"kind": "text_delta", "text": pending})
    if not final:
        raise OllamaError("asmrun closed the stream without its final line", status_code=200)
    for call in final.get("tool_calls") or []:
        function = call.get("function") if isinstance(call, dict) else None
        if isinstance(function, dict) and function.get("name"):
            report({"kind": "tool_call_requested", "name": function["name"], "arguments": function.get("arguments")})
    return body, _response(final, model_key, started)

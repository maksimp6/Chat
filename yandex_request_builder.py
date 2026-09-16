"""Request payload helpers for the Yandex Responses API.

This module contains pure request-shaping logic extracted from yandex_client.py.
It deliberately has no HTTP, database, tracing, or MCP side effects.
"""


def build_response_payload(project_id, model_key, message, params=None, metadata=None, conversation_id=None, yandex_conv_id=None):
    """Build a Yandex Responses API payload from client parameters."""
    params = params or {}
    is_background = params.get("background", True)

    if is_background:
        params["store"] = True

    payload = {
        "model": "gpt://" + project_id + "/" + model_key + "/latest",
        "input": params.get("input") or [{"role": "user", "content": message}],
        "background": is_background,
        "store": params.get("store", True),
    }

    if metadata is not None:
        payload["metadata"] = metadata

    if params.get("instructions"):
        payload["instructions"] = params["instructions"]
    if params.get("temperature") is not None:
        payload["temperature"] = float(params["temperature"])
    if params.get("top_p") is not None:
        payload["top_p"] = float(params["top_p"])
    if params.get("max_output_tokens"):
        payload["max_output_tokens"] = int(params["max_output_tokens"])
    if params.get("prompt"):
        payload["prompt"] = params["prompt"]
    if params.get("text"):
        payload["text"] = params["text"]
    if params.get("truncation"):
        payload["truncation"] = params["truncation"]
    if params.get("service_tier"):
        payload["service_tier"] = params["service_tier"]

    cache_key = params.get("prompt_cache_key") or conversation_id
    if cache_key:
        payload["prompt_cache_key"] = str(cache_key)

    if params.get("reasoning"):
        payload["reasoning"] = params["reasoning"]
    elif params.get("reasoning_effort") and params["reasoning_effort"] != "disabled":
        payload["reasoning"] = {"effort": params["reasoning_effort"]}

    raw_tools = params.get("tools")
    if raw_tools:
        cleaned_tools = _clean_tools(raw_tools)
        if cleaned_tools:
            payload["tools"] = cleaned_tools
            if params.get("tool_choice"):
                payload["tool_choice"] = params["tool_choice"]
            if params.get("max_tool_calls"):
                payload["max_tool_calls"] = int(params["max_tool_calls"])

            ptc = params.get("parallel_tool_calls")
            if ptc is not None:
                if isinstance(ptc, str):
                    ptc = ptc.lower() not in ("false", "0")
                payload["parallel_tool_calls"] = bool(ptc)
            else:
                payload["parallel_tool_calls"] = True

    if yandex_conv_id:
        payload["conversation"] = {"id": yandex_conv_id}

    return payload


def _clean_mcp_tool(tool):
    if not isinstance(tool, dict) or tool.get("type") != "mcp":
        return None
    cid = tool.get("connector_id", "")
    url = tool.get("server_url", "").strip()
    if not url and not cid.startswith("connector_"):
        return None
    cleaned = {"type": "mcp", "server_label": tool.get("server_label", "mcp_server")}
    if url:
        cleaned["server_url"] = url
    if cid:
        cleaned["connector_id"] = cid
    if tool.get("server_description"):
        cleaned["server_description"] = tool["server_description"]
    if tool.get("require_approval"):
        cleaned["require_approval"] = tool["require_approval"]
    if tool.get("authorization"):
        cleaned["authorization"] = tool["authorization"]
    if tool.get("headers"):
        cleaned["headers"] = tool["headers"]
    return cleaned


def _clean_tools(tools):
    if not isinstance(tools, list):
        return tools
    cleaned = []
    for tool in tools:
        if isinstance(tool, dict) and tool.get("type") == "mcp":
            result = _clean_mcp_tool(tool)
            if result is not None:
                cleaned.append(result)
        else:
            cleaned.append(tool)
    return cleaned

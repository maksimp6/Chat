"""Request payload helpers for the Yandex Responses API.

This module contains pure request-shaping logic extracted from yandex_client_modules/client.py.
It deliberately has no HTTP, database, tracing, or MCP side effects.
"""

from core.config import upstream_model_id
from yandex_client_modules.errors import YandexClientError
from core.model_profiles import ultra_profiles, ultra_reasoning_controllable, ultra_request_defaults
from yandex_client_modules.metadata_validator import validate_metadata


TEMPERATURE_MIN = 0.0
TEMPERATURE_MAX = 1.0
LEGACY_OUTPUT_CAP_TOKENS = 2000


def validate_generation_params(params):
    """Validate provider generation parameters before building an outbound request."""
    params = params or {}
    if params.get("temperature") is None:
        return params
    try:
        temperature = float(params["temperature"])
    except (TypeError, ValueError) as exc:
        raise ValueError("temperature must be a number between 0 and 1") from exc
    if not TEMPERATURE_MIN <= temperature <= TEMPERATURE_MAX:
        raise ValueError(f"temperature must be between {TEMPERATURE_MIN:g} and {TEMPERATURE_MAX:g}")
    params["temperature"] = temperature
    return params


def _response_input(params, message):
    """Text-only input. Photos never reach this path: request_mixin sends them through Chat Completions."""
    if params.get("input"):
        return params["input"]
    return [{"role": "user", "content": message}]


# Every request parameter is named the way an Ultra profile names each setting: the value that
# was sent, or the reason it was left to the provider. Nothing is silently absent.
_RESPONSES_PARAMETERS = (
    "background",
    "store",
    "stream",
    "temperature",
    "top_p",
    "max_output_tokens",
    "truncation",
    "reasoning",
    "service_tier",
    "tool_choice",
    "max_tool_calls",
    "parallel_tool_calls",
)
_CHAT_PARAMETERS = ("stream", "temperature", "top_p", "max_tokens", "tools")
_TOOL_PARAMETERS = {"tool_choice", "max_tool_calls", "parallel_tool_calls"}


def explicit_parameters(model_key, payload):
    """Each parameter of the final payload with its sent value, or why it is not sent."""
    profile = ultra_profiles().get(model_key) or {}
    output = profile.get("output") or {}
    reasoning = profile.get("reasoning") or {}
    result = {}
    for name in _CHAT_PARAMETERS if "messages" in payload else _RESPONSES_PARAMETERS:
        if name in payload:
            result[name] = {"sent": True, "value": payload[name]}
            continue
        if name in ("max_output_tokens", "max_tokens"):
            reason = output.get("basis") or "no_application_output_cap"
        elif name == "reasoning":
            reason = reasoning.get("basis") if reasoning.get("depth_control") is False else "reasoning_effort_disabled"
        elif name == "tools":
            reason = "photo_request_has_no_tool_loop"
        elif name in _TOOL_PARAMETERS:
            reason = "no_tools_in_request"
        elif name == "service_tier":
            reason = "provider_default"
        else:
            reason = "not_set_by_request"
        result[name] = {"sent": False, "value": None, "reason": reason}
    return result


def ultra_applied_event(model_key, params, payload):
    """Describe the Ultra values that reached the provider, so a trace can confirm the chosen profile."""
    profile = ultra_profiles().get(model_key) or {}
    return {
        "model": model_key,
        "catalog_profile_id": profile.get("profile_id"),
        "requested_profile_id": (params or {}).get("model_profile_id"),
        "reasoning": payload.get("reasoning"),
        "truncation": payload.get("truncation"),
        "max_output_tokens": payload.get("max_output_tokens"),
        "parameters": explicit_parameters(model_key, payload),
    }


def build_chat_image_payload(
    project_id,
    model_key,
    message,
    images,
    instructions=None,
    temperature=None,
    top_p=None,
    max_tokens=None,
    history=None,
):
    """Chat Completions body for a message with photos, in the shape the Yandex docs show for image input.

    ``history`` is the earlier turns of the conversation as {"role", "text"} items; Chat Completions
    keeps no server-side state, so the earlier turns must travel with every request.
    """
    content = [{"type": "text", "text": message}]
    content += [{"type": "image_url", "image_url": {"url": url}} for url in images]
    messages = []
    if instructions:
        messages.append({"role": "system", "content": instructions})
    for turn in history or []:
        messages.append({"role": turn["role"], "content": turn["text"]})
    messages.append({"role": "user", "content": content})
    payload = {
        "model": "gpt://" + project_id + "/" + upstream_model_id(model_key),
        "messages": messages,
        # The answer is read as one JSON body, never as a stream.
        "stream": False,
    }
    if temperature is not None:
        payload["temperature"] = float(temperature)
    if top_p is not None:
        payload["top_p"] = float(top_p)
    if max_tokens:
        payload["max_tokens"] = int(max_tokens)
    return payload


def chat_completion_to_response(data):
    """Reshape a Chat Completions answer into the Responses shape the rest of the client reads.

    The finish reason decides the status: a truncated answer is "incomplete" with its reason kept,
    a filtered or empty answer is an error, so a partial text never passes as a finished one.
    """
    choices = data.get("choices") if isinstance(data, dict) else None
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise YandexClientError("chat completion has no choices")
    first = choices[0]
    finish_reason = first.get("finish_reason")
    text = (first.get("message") or {}).get("content")
    if finish_reason == "content_filter":
        raise YandexClientError("answer was withheld by the content filter")
    if not isinstance(text, str) or not text.strip():
        raise YandexClientError("chat completion returned no text")
    usage = data.get("usage") or {}
    incomplete = finish_reason == "length"
    return {
        "id": data.get("id"),
        "status": "incomplete" if incomplete else "completed",
        "incomplete_details": {"reason": finish_reason} if incomplete else None,
        "finish_reason": finish_reason,
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text}],
            }
        ],
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
        },
    }


def build_response_payload(
    project_id,
    model_key,
    message,
    params=None,
    metadata=None,
    conversation_id=None,
    yandex_conv_id=None,
):
    """Build a Yandex Responses API payload from client parameters."""
    # A copy, so applying the model profile never mutates saved caller settings.
    params = dict(params or {})
    defaults = ultra_request_defaults(model_key)
    if defaults:
        # Older browsers still send medium reasoning, auto truncation and output
        # caps. The model's Ultra policy owns these fields, except an output cap
        # the user set explicitly (0 means no cap). The old 2000 default is not
        # a user choice; the browser drops it too, so the sent value matches the
        # value shown in settings.
        user_cap = int(params.get("max_output_tokens") or 0)
        if user_cap == LEGACY_OUTPUT_CAP_TOKENS:
            user_cap = 0
        params.update(defaults)
        if user_cap > 0:
            params["max_output_tokens"] = user_cap
        params.pop("reasoning", None)
    is_background = params.get("background", True)

    if is_background:
        params["store"] = True

    payload = {
        "model": "gpt://" + project_id + "/" + upstream_model_id(model_key) + "/latest",
        "input": _response_input(params, message),
        "background": is_background,
        "store": params.get("store", True),
        # The reply is polled, never read as a stream, so a stream request is never sent.
        "stream": False,
    }

    validated_metadata = validate_metadata(metadata)
    if validated_metadata is not None:
        payload["metadata"] = validated_metadata

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
    elif (
        params.get("reasoning_effort")
        and params["reasoning_effort"] != "disabled"
        and ultra_reasoning_controllable(model_key)
    ):
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
    cid = str(tool.get("connector_id") or "")
    url = str(tool.get("server_url") or "").strip()
    # The server credential is sent to the endpoint, so it may only go to an https URL.
    if url and not url.lower().startswith("https://"):
        return None
    if not url and not cid.startswith("connector_"):
        return None
    cleaned = {"type": "mcp", "server_label": tool.get("server_label", "mcp_server")}
    if url:
        cleaned["server_url"] = url
    if cid:
        cleaned["connector_id"] = cid
    if tool.get("server_description"):
        cleaned["server_description"] = tool["server_description"]
    # The application runs MCP tools without an approval pause. The provider
    # contract accepts "never" and "always"; stale saved policy cannot restore a gate.
    cleaned["require_approval"] = "never"
    if tool.get("authorization"):
        cleaned["authorization"] = tool["authorization"]
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

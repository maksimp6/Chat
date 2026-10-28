"""Request payload helpers for the Yandex Responses API.

This module contains pure request-shaping logic extracted from yandex_client_modules/client.py.
It deliberately has no HTTP, database, tracing, or MCP side effects.
"""

import math

from core.config import get_model_uri
from yandex_client_modules.errors import YandexClientError
from core.model_profiles import ultra_profiles, ultra_reasoning_controllable, ultra_request_defaults
from yandex_client_modules.metadata_validator import validate_metadata


TEMPERATURE_MIN = 0.0
TEMPERATURE_MAX = 2.0
_REASONING_EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh"}


def validate_generation_params(params):
    """Validate without coercing strings/bools or mutating the caller's settings.

    Numeric ranges follow the Responses request schema, not a model-specific
    capability claim. Zero output tokens is the application's explicit no-cap
    sentinel; it is omitted from the provider request.
    """
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise ValueError("params must be an object")
    validated = dict(params)
    for name, minimum, maximum in (
        ("temperature", TEMPERATURE_MIN, TEMPERATURE_MAX),
        ("top_p", 0.0, 1.0),
    ):
        value = validated.get(name)
        if value is None:
            continue
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not minimum <= value <= maximum
            or not math.isfinite(value)
        ):
            raise ValueError(f"{name} must be a finite number between {minimum:g} and {maximum:g}")
        validated[name] = float(value)
    for name in ("background", "store", "stream", "parallel_tool_calls"):
        if name in validated and not isinstance(validated[name], bool):
            raise ValueError(f"{name} must be a boolean")
    if validated.get("stream") is True:
        raise ValueError("stream=true is not supported by the polling transport")
    if validated.get("background", True) and validated.get("store") is False:
        raise ValueError("background=true requires store=true")
    limit = validated.get("max_output_tokens")
    if limit is not None and (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or (limit != 0 and limit < 16)
    ):
        raise ValueError("max_output_tokens must be 0 (no application cap) or an integer of at least 16")
    tool_limit = validated.get("max_tool_calls")
    if tool_limit is not None and (
        isinstance(tool_limit, bool) or not isinstance(tool_limit, int) or tool_limit < 0
    ):
        raise ValueError("max_tool_calls must be null (provider default) or a non-negative integer")
    if "top_logprobs" in validated:
        value = validated["top_logprobs"]
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 20:
            raise ValueError("top_logprobs must be an integer between 0 and 20")
    if "previous_response_id" in validated:
        value = validated["previous_response_id"]
        if not isinstance(value, str) or not value.strip():
            raise ValueError("previous_response_id must be a non-empty string")
    for name in ("instructions", "prompt_cache_key"):
        if name in validated and not isinstance(validated[name], str):
            raise ValueError(f"{name} must be a string")
    if "reasoning_effort" in validated:
        effort = validated["reasoning_effort"]
        if not isinstance(effort, str) or effort not in _REASONING_EFFORTS | {"disabled"}:
            raise ValueError("reasoning_effort must be disabled, none, minimal, low, medium, high or xhigh")
    if "reasoning" in validated:
        reasoning = validated["reasoning"]
        if not isinstance(reasoning, dict):
            raise ValueError("reasoning must be an object")
        if "effort" in reasoning:
            effort = reasoning["effort"]
            if not isinstance(effort, str) or effort not in _REASONING_EFFORTS:
                raise ValueError("reasoning.effort must be none, minimal, low, medium, high or xhigh")
    for name, allowed in (
        ("truncation", {"auto", "disabled"}),
        ("service_tier", {"auto", "default", "flex", "scale", "priority"}),
    ):
        if name in validated:
            value = validated[name]
            if not isinstance(value, str) or value not in allowed:
                raise ValueError(f"{name} must be one of {', '.join(sorted(allowed))}")
    if "text" in validated:
        text = validated["text"]
        if not isinstance(text, dict):
            raise ValueError("text must be an object")
        if "verbosity" in text:
            verbosity = text["verbosity"]
            if not isinstance(verbosity, str) or verbosity not in {"low", "medium", "high"}:
                raise ValueError("text.verbosity must be low, medium or high")
        if "format" in text:
            text_format = text["format"]
            if not isinstance(text_format, dict):
                raise ValueError("text.format must be an object")
            format_type = text_format.get("type")
            if not isinstance(format_type, str) or format_type not in {"text", "json_schema", "json_object"}:
                raise ValueError("text.format.type must be text, json_schema or json_object")
            if format_type == "json_schema":
                if not isinstance(text_format.get("schema"), dict):
                    raise ValueError("text.format.schema must be an object")
                name = text_format.get("name")
                if not isinstance(name, str) or not name.strip():
                    raise ValueError("text.format.name must be a non-empty string")
                if "strict" in text_format and not isinstance(text_format["strict"], bool):
                    raise ValueError("text.format.strict must be a boolean")
                if "description" in text_format and not isinstance(text_format["description"], str):
                    raise ValueError("text.format.description must be a string")
    if "tool_choice" in validated:
        choice = validated["tool_choice"]
        if isinstance(choice, str):
            if choice not in {"auto", "none", "required"}:
                raise ValueError("tool_choice must be auto, none, required or a typed object")
        elif isinstance(choice, dict):
            choice_type = choice.get("type")
            if not isinstance(choice_type, str) or not choice_type.strip():
                raise ValueError("tool_choice.type must be a non-empty string")
            if choice_type == "function" and (
                not isinstance(choice.get("name"), str) or not choice["name"].strip()
            ):
                raise ValueError("tool_choice.name must be a non-empty string for a function")
        else:
            raise ValueError("tool_choice must be auto, none, required or a typed object")
    return validated


# WIKIFUNCTIONS-CANDIDATE WF-06: https://www.wikifunctions.org/wiki/Z873 + Z18755.
# Заменить сборку content композицией map(images) + concat(text, images); адаптер
# сохраняет готовый params.input как есть, порядок, detail="auto" и фото только в первом ходе.
def _response_input(params, message):
    """Keep tool continuations intact; attach photos only to the initial user turn."""
    if "input" in params:
        return params["input"]
    images = params.get("input_images") or []
    if images:
        content = [{"type": "input_text", "text": message}] if message else []
        content.extend(
            {"type": "input_image", "image_url": image, "detail": "auto"}
            for image in images
        )
        return [{"role": "user", "content": content}]
    return [{"role": "user", "content": message}]


# Every request parameter is named the way an Ultra profile names each setting: the value that
# was sent, or the reason it was left to the provider. Nothing is silently absent.
_RESPONSES_PARAMETERS = (
    "input",
    "instructions",
    "background",
    "store",
    "stream",
    "temperature",
    "top_p",
    "top_logprobs",
    "max_output_tokens",
    "truncation",
    "reasoning",
    "service_tier",
    "text",
    "prompt_cache_key",
    "prompt",
    "previous_response_id",
    "conversation",
    "tools",
    "tool_choice",
    "max_tool_calls",
    "parallel_tool_calls",
)
_CHAT_PARAMETERS = ("stream", "temperature", "top_p", "max_tokens", "tools")
_TOOL_PARAMETERS = {"tool_choice", "max_tool_calls", "parallel_tool_calls"}


# WIKIFUNCTIONS-CANDIDATE WF-20: https://www.wikifunctions.org/wiki/Z872 + Z873.
# Заменить ядро сводки tools/input на filter/map; адаптер сохраняет ветки name,
# strict is True, пределы 128/32/50, count и возврат остальных значений без копирования.
def _sent_value(name, value):
    """The value a trace shows: tools and input by count and kind, since their full bodies are in the request manifest."""
    if name == "tools" and isinstance(value, list):
        listed = [tool for tool in value if isinstance(tool, dict)]
        return {
            "count": len(listed),
            "names": [str(tool.get("name") or tool.get("type") or "")[:128] for tool in listed],
            "strict": sum(1 for tool in listed if tool.get("strict") is True),
        }
    if name == "input" and isinstance(value, list):
        return {
            "count": len(value),
            "types": [str(item.get("type") or item.get("role") or "")[:32] if isinstance(item, dict) else "" for item in value][:50],
        }
    return value


def explicit_parameters(model_key, payload):
    """Each parameter of the final payload with its sent value, or why it is not sent."""
    profile = ultra_profiles().get(model_key) or {}
    output = profile.get("output") or {}
    reasoning = profile.get("reasoning") or {}
    result = {}
    for name in _CHAT_PARAMETERS if "messages" in payload else _RESPONSES_PARAMETERS:
        if name in payload:
            result[name] = {"sent": True, "value": _sent_value(name, payload[name])}
            continue
        if name in ("max_output_tokens", "max_tokens"):
            reason = output.get("basis") or "no_application_output_cap"
        elif name == "reasoning":
            reason = reasoning.get("basis") if reasoning.get("depth_control") is False else "reasoning_effort_disabled"
        elif name == "tools":
            reason = "photo_request_has_no_tool_loop" if "messages" in payload else "no_tools_in_request"
        elif name == "max_tool_calls" and payload.get("tools"):
            reason = "provider_default"
        elif name in _TOOL_PARAMETERS:
            reason = "no_tools_in_request"
        elif name == "conversation":
            reason = "no_provider_conversation"
        elif name == "service_tier":
            reason = "provider_default"
        else:
            reason = "not_set_by_request"
        result[name] = {"sent": False, "value": None, "reason": reason}
    return result


def ultra_applied_event(model_key, params, payload):
    """Describe assembled parameters; this event does not prove provider acceptance."""
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
        "model": get_model_uri(project_id, model_key),
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
    params = validate_generation_params(params)
    defaults = ultra_request_defaults(model_key)
    if defaults:
        # The Ultra policy owns profile defaults, but an explicit positive cap
        # is preserved. Its value alone cannot identify an old saved default.
        user_cap = params.get("max_output_tokens") or 0
        params.update(defaults)
        if user_cap > 0:
            params["max_output_tokens"] = user_cap
        params.pop("reasoning", None)
    is_background = params.get("background", True)

    if is_background:
        params["store"] = True

    payload = {
        "model": get_model_uri(project_id, model_key),
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
    if "top_logprobs" in params:
        payload["top_logprobs"] = params["top_logprobs"]
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
    cleaned_tools = _clean_tools(raw_tools) if raw_tools is not None else []
    tool_choice = params.get("tool_choice", "auto")
    if not cleaned_tools and (tool_choice == "required" or isinstance(tool_choice, dict)):
        raise ValueError("tool_choice requires an available tool in tools")
    if isinstance(tool_choice, dict) and tool_choice.get("type") == "function":
        if not any(
            tool.get("type") == "function" and tool.get("name") == tool_choice["name"]
            for tool in cleaned_tools
        ):
            raise ValueError("tool_choice.name must name an available function in tools")
    if cleaned_tools:
        payload["tools"] = cleaned_tools
        payload["tool_choice"] = tool_choice
        max_tool_calls = params.get("max_tool_calls")
        # The input schema documents an integer. Omit an unset limit rather
        # than relying on nullable response fields to prove POST support.
        if max_tool_calls is not None:
            payload["max_tool_calls"] = max_tool_calls

        ptc = params.get("parallel_tool_calls")
        if ptc is not None:
            payload["parallel_tool_calls"] = ptc
        else:
            payload["parallel_tool_calls"] = True

    previous_response_id = params.get("previous_response_id")
    if previous_response_id and yandex_conv_id:
        raise ValueError("previous_response_id cannot be combined with a provider conversation")
    if previous_response_id:
        payload["previous_response_id"] = previous_response_id
    if yandex_conv_id:
        payload["conversation"] = yandex_conv_id

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


# WIKIFUNCTIONS-CANDIDATE WF-07: https://www.wikifunctions.org/wiki/Z873 + Z872.
# Заменить обход на map(validate/clean) + filter(result is not None); адаптер сохраняет
# ValueError для malformed tools, HTTPS-политику _clean_mcp_tool и исходные non-MCP объекты.
def _clean_tools(tools):
    if not isinstance(tools, list):
        raise ValueError("tools must be a list of typed objects")
    cleaned = []
    for tool in tools:
        if (
            not isinstance(tool, dict)
            or not isinstance(tool.get("type"), str)
            or not tool["type"].strip()
        ):
            raise ValueError("tools must be a list of typed objects")
        if tool.get("type") == "mcp":
            result = _clean_mcp_tool(tool)
            if result is not None:
                cleaned.append(result)
        else:
            cleaned.append(tool)
    return cleaned

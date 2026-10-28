"""Маршруты чата с сохранением полной цепочки выполнения в БД."""

from flask import Blueprint, request, jsonify
import json as _json
import threading
import logging
import re
import uuid
from typing import Any
from dataclasses import dataclass
import base64
import functools
from chat._error_details import (
    EMPTY_MODEL_REPLY,
    PROVIDER_KEY_EXPIRED,
    PROVIDER_KEY_MISSING,
    error_body,
)
from chat._vendor.partial_output import (
    extract_last_response_text,
    format_partial_output_message,
)
from chat._vendor.provider_receipt import capture_provider_diagnostics
from chat._vendor.responses_tool_loop import run_tool_loop, extract_function_calls
from chat.host import (
    add_message,
    aggregate_billing,
    bind_current_trace,
    bound,
    build_ai_billing_item,
    check_access,
    completion_model_keys,
    compose_skill_instructions,
    create_conversation,
    create_invocation,
    create_invocation_trace,
    fail_invocation,
    file_court_case,
    finish_invocation,
    get_conv_settings,
    get_conversation_title,
    get_current_owner_id,
    get_message,
    get_messages,
    get_yandex_conversation,
    is_provider_pending,
    list_owned_conversations,
    maybe_update_conversation_title,
    normalize_conversation_title,
    normalize_tools_config,
    persist_invocation_trace,
    record_yandex_mcp_activity,
    reset_current_trace,
    save_conv_settings,
    upgrade_dialog_settings,
    set_owner,
    set_yandex_conversation,
    settle_billing_to_treasury,
    start_invocation,
    validate_dialog_model,
    validate_generation_params,
)

logger = logging.getLogger("logos.chat.mcp_routes")
mcp_bp = Blueprint("mcp", __name__)
_skill_registry = None
_lazy_lock = threading.Lock()


def skill_registry():
    """The skill registry, created once on first use."""
    global _skill_registry
    with _lazy_lock:
        if _skill_registry is None:
            _skill_registry = bound.SkillRegistry()
        return _skill_registry


# Per-response polling budget; it is not a deadline for the whole tool loop.
CHAT_POLL_TIMEOUT_SECONDS = 240


def _tool_rounds(value: Any) -> int | None:
    """Only an explicit round limit bounds the local tool loop."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("max_rounds must be a positive integer or null")
    return value


def _tool_call_parts(tool_call: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
    function = tool_call.get("function")
    if not isinstance(function, dict):
        function = {}
    name = tool_call.get("name") or function.get("name")
    if name and "<|" in name:
        name = name.split("<|", 1)[0].strip()

    arguments = (
        tool_call["arguments"]
        if "arguments" in tool_call
        else function.get("arguments", {})
    )
    if isinstance(arguments, str):
        try:
            arguments = _json.loads(arguments)
        except ValueError:
            raise ValueError(
                "Invalid tool arguments: expected a valid JSON object"
            ) from None
    if not isinstance(arguments, dict):
        raise ValueError("Invalid tool arguments: expected a JSON object")
    return name, arguments


# WIKIFUNCTIONS-CANDIDATE WF-05: https://www.wikifunctions.org/wiki/Z873 + Z872 + Z10079.
# Заменить ядро очистки списка на map(trim) + filter(nonempty); адаптер сохраняет
# приоритет data.skills, params.pop только при fallback, проверку list[str] и дубли.
def _normalize_requested_skills(data, params):
    raw = data.get("skills")
    if raw is None:
        raw = params.pop("skills", [])
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise bound.SkillRegistryError("skills must be a string or a list of strings")
    return [item.strip() for item in raw if item.strip()]


# Generation and transport settings the browser may choose for a request. The server owns the
# request shape: the input, the tool list, the tool categories and the tool configuration come
# from the conversation and the registry, never from the request body.
_CLIENT_PARAM_KEYS = frozenset(
    {
        "background",
        "instructions",
        "max_output_tokens",
        "max_rounds",
        "max_tool_calls",
        "parallel_tool_calls",
        "poll_timeout",
        "previous_response_id",
        "prompt_cache_key",
        "reasoning",
        "reasoning_effort",
        "request_timeout",
        "service_tier",
        "store",
        "stream",
        "temperature",
        "text",
        "tool_choice",
        "top_p",
        "top_logprobs",
        "truncation",
    }
)


def _client_params(raw: dict[str, Any]) -> dict[str, Any]:
    unsupported = set(raw) - _CLIENT_PARAM_KEYS
    if unsupported:
        raise ValueError("unsupported params: " + ", ".join(sorted(unsupported)))
    params = validate_generation_params(raw)
    if params.get("previous_response_id"):
        raise ValueError(
            "previous_response_id cannot be combined with this endpoint's conversation; clear it to use /api/chat"
        )
    params["max_rounds"] = _tool_rounds(params.get("max_rounds"))
    # Validate transport settings before storing a message or starting provider work.
    params["request_timeout"] = bound._transport_timeout(
        params, "request_timeout", 90, 900
    )
    params["poll_timeout"] = bound._transport_timeout(
        params, "poll_timeout", CHAT_POLL_TIMEOUT_SECONDS, 7200
    )
    return params


# Sent when the provider stops a reply at its output limit: the answer goes on, not restarts.
OUTPUT_LIMIT_CONTINUE_PROMPT = (
    "Твой предыдущий ответ оборвался из-за ограничения длины вывода. Продолжи его "
    "ровно с того места, где он прервался: без повторов, без вступления и без пояснений."
)


def _cut_by_output_limit(response) -> bool:
    """True when the provider stopped this response at its output-token limit."""
    if not isinstance(response, dict) or response.get("status") != "incomplete":
        return False
    details = response.get("incomplete_details")
    return isinstance(details, dict) and details.get("reason") == "max_output_tokens"


def _join_continued_parts(parts, last):
    """One response whose output is every part in order, so the reply text is whole.

    Earlier parts were cut mid-way: their unfinished function calls are dropped, so
    only the last part can still ask for a tool.
    """
    output = []
    for part in parts:
        for item in part.get("output") or []:
            if not isinstance(item, dict) or item.get("type") == "function_call":
                continue
            content = item.get("content")
            if item.get("type") == "message" and isinstance(content, list):
                item = {
                    **item,
                    "content": [
                        piece
                        for piece in content
                        if not (
                            isinstance(piece, dict)
                            and piece.get("type") == "function_call"
                        )
                    ],
                }
            output.append(item)
    joined = dict(last) if isinstance(last, dict) else {}
    joined["output"] = output + list(joined.get("output") or [])
    joined["continued_response_ids"] = [part.get("id") for part in parts]
    return joined


def _record_provider_output(trace, response):
    """What the provider returned for one request: the status, the output kinds and the calls it asked for."""
    if not isinstance(trace, bound.ExecutionTrace) or not isinstance(response, dict):
        return
    output = response.get("output")
    if not isinstance(output, list):
        output = []
    trace.add_event(
        "provider_output_observed",
        {
            "status": response.get("status"),
            "output_types": [
                str(item.get("type"))[:64] for item in output if isinstance(item, dict)
            ][:50],
            "function_calls": [
                str(call.get("name") or "")[:128]
                for call in extract_function_calls(response)
            ][:50],
        },
    )


# Models whose registry entry says they read images. A message with photos to any other
# model is refused instead of being sent without them.
@functools.cache
def image_input_model_keys() -> frozenset[str]:
    return frozenset(
        key
        for key, model in bound.TEXT_MODELS.items()
        if (model.get("capabilities") or {}).get("multimodal") is True
    )


IMAGE_HISTORY_TURNS = 20
IMAGE_HISTORY_TEXT_CHARS = 4000
_MARKDOWN_PICTURE = re.compile(r"!\[[^\]]*\]\(data:image/[^)]*\)")


def _image_turn_history(conv_id):
    """Earlier turns for a Chat Completions request: text only, pictures replaced by a marker."""
    turns = [m for m in get_messages(conv_id) if m.get("role") in ("user", "assistant")]
    if turns and turns[-1]["role"] == "user":
        turns = turns[:-1]  # the current message is sent separately
    history = []
    for turn in turns[-IMAGE_HISTORY_TURNS:]:
        text = _MARKDOWN_PICTURE.sub("[картинка]", str(turn.get("text") or ""))[
            :IMAGE_HISTORY_TEXT_CHARS
        ]
        if text.strip():
            history.append({"role": turn["role"], "text": text})
    return history


MAX_IMAGES_PER_MESSAGE = 4
MAX_IMAGE_BYTES = 5 * 1024 * 1024


def _detect_image_mime(image_bytes: bytes) -> str | None:
    """Return the image MIME type implied by the magic bytes, or None.

    The declared MIME in a data URL is set by the client from the browser's
    File.type, which can disagree with the actual bytes (e.g. a JPEG staged as
    image/png). Detect the real type from the content so the provider always
    receives a data URL whose declared type matches its bytes.
    """
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if image_bytes.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
        return "image/webp"
    return None


def _validated_images(raw: Any) -> list[str]:
    """Return the attached images as data URLs, or raise ValueError for a bad attachment."""
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > MAX_IMAGES_PER_MESSAGE:
        raise ValueError(
            f"attachments must be a list of at most {MAX_IMAGES_PER_MESSAGE} images"
        )
    data_urls = []
    for item in raw:
        url = item.get("data_url") if isinstance(item, dict) else None
        if not isinstance(url, str):
            raise ValueError("each attachment must have a data_url string")
        header, separator, encoded = url.partition(",")
        if (
            not separator
            or not header.startswith("data:")
            or not header.endswith(";base64")
        ):
            raise ValueError("each attachment must be a base64 data URL")
        if len(encoded) > MAX_IMAGE_BYTES * 4 // 3 + 4:
            raise ValueError("each image must be at most 5 MB")
        image_bytes = base64.b64decode(encoded, validate=True)
        if len(image_bytes) > MAX_IMAGE_BYTES:
            raise ValueError("each image must be at most 5 MB")
        mime = header[len("data:") : -len(";base64")]
        detected = _detect_image_mime(image_bytes)
        if detected is None:
            raise ValueError("unsupported image format")
        # The provider must receive a data URL whose declared type matches the
        # bytes. Rebuild it from the detected MIME when the client mislabeled it.
        if detected != mime:
            url = f"data:{detected};base64,{encoded}"
        data_urls.append(url)
    return data_urls


def _define_logos_client(base: type) -> type:
    """The chat client class on top of the provider client (built once, on first use)."""

    class LogosClient(base):
        def ask(
            self,
            message: Any,
            model_key: Any,
            conversation_id: Any = None,
            params: Any = None,
            execution_trace: Any = None,
            trace_step: Any = None,
        ) -> Any:
            return super().ask(
                message,
                model_key,
                conversation_id,
                params,
                execution_trace=execution_trace,
                trace_step=trace_step,
            )

        def _ask_provider(self, **kwargs: Any) -> Any:
            return super().ask_with_mcp(**kwargs)

        # Logos Pro shopping policy: never source recommendations from Yandex Market.
        # Keep Yandex AI Studio and unrelated Yandex services available.
        mandatory_blocked_web_domains = (
            "market.yandex.ru",
            "market.yandex.com",
            "market.yandex.kz",
            "market.yandex.by",
        )

        def ask_with_mcp(
            self,
            message,
            model_key,
            conversation_id=None,
            params=None,
            trace=None,
            *,
            execution_policy=None,
            settings_conversation_id=None,
        ):
            policy_kwargs = (
                {"execution_policy": execution_policy}
                if execution_policy is not None
                else {}
            )

            response = self._ask_provider(
                message=message,
                model_key=model_key,
                conversation_id=conversation_id,
                settings_conversation_id=settings_conversation_id,
                params=params,
                trace=trace,
                **policy_kwargs,
            )
            _record_provider_output(trace, response)

            base_params = dict(params or {})
            tool_servers = None

            def execute_tool(call):
                nonlocal tool_servers
                if tool_servers is None:
                    tool_servers = bound.mcp_storage.list_servers(
                        owner_id=get_current_owner_id()
                    )
                result = self._execute_single_tool(
                    call, tool_servers, trace=trace, **policy_kwargs
                )
                if result.get("error") is not None:
                    return {"error": result["error"], "tool": result.get("name")}
                return result.get("result")

            # Retain the original request and all tool rounds without a provider conversation.
            initial_input = bound._response_input(base_params, message)
            continuation_history = (
                [{"role": "user", "content": initial_input}]
                if isinstance(initial_input, str)
                else list(initial_input)
            )

            def follow_up(input_items):
                """One more request of this turn, with the forced tool choice released."""
                continuation_params = dict(base_params)
                # The initial forced choice has been satisfied. Permit a final
                # answer instead of forcing the same action again with a new id.
                choice = continuation_params.get("tool_choice")
                if choice == "required":
                    continuation_params["tool_choice"] = "auto"
                elif isinstance(choice, dict):
                    continuation_params["tool_choice"] = (
                        {**choice, "mode": "auto"}
                        if choice.get("type") == "allowed_tools"
                        else "auto"
                    )
                continuation_params["input"] = input_items
                continuation_params["background"] = base_params.get(
                    "background", True
                )
                response = self._ask_provider(
                    message="",
                    model_key=model_key,
                    conversation_id=conversation_id,
                    settings_conversation_id=settings_conversation_id,
                    params=continuation_params,
                    trace=trace,
                    **policy_kwargs,
                )
                _record_provider_output(trace, response)
                return response

            def continue_request(input_items, previous_response):
                # The provider conversation already contains the answered calls.
                if conversation_id:
                    return follow_up(
                        [
                            item
                            for item in input_items
                            if isinstance(item, dict)
                            and item.get("type") == "function_call_output"
                        ]
                    )
                continuation_history.extend(input_items)
                return follow_up(list(continuation_history))

            def continue_after_output_limit(previous_response):
                # The provider conversation holds the cut answer; otherwise it joins the history.
                request = {"role": "user", "content": OUTPUT_LIMIT_CONTINUE_PROMPT}
                if conversation_id:
                    return follow_up([request])
                continuation_history.extend(
                    list(previous_response.get("output") or []) + [request]
                )
                return follow_up(list(continuation_history))

            def run_tools(response):
                if not extract_function_calls(response):
                    return response
                return run_tool_loop(
                    response,
                    execute_tool,
                    continue_request,
                    max_rounds=_tool_rounds(base_params.get("max_rounds")),
                )

            response = run_tools(response)
            # A reply cut by the provider's output limit goes on where it stopped, part by
            # part, until it is complete; only a part that adds no output ends the chain.
            parts = []
            while _cut_by_output_limit(response) and response.get("output"):
                parts.append(response)
                if trace is not None:
                    trace.add_event(
                        "provider_output_limit_continued",
                        {"part": len(parts), "response_id": response.get("id")},
                    )
                response = run_tools(continue_after_output_limit(response))
            if parts:
                response = _join_continued_parts(parts, response)

            if trace is not None:
                record_yandex_mcp_activity(trace)
            return response

    return LogosClient


_logos_client_type = None


def logos_client_type() -> type:
    """The chat's provider client class."""
    global _logos_client_type
    with _lazy_lock:
        if _logos_client_type is None:
            _logos_client_type = _define_logos_client(bound.YandexResponsesClient)
        return _logos_client_type


def __getattr__(name: str) -> Any:
    """Compatibility for ``from chat.mcp_routes import LogosClient`` / ``IMAGE_INPUT_MODEL_KEYS``."""
    if name == "LogosClient":
        return logos_client_type()
    if name == "IMAGE_INPUT_MODEL_KEYS":
        return image_input_model_keys()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _turn_cost(billing: dict[str, Any]) -> float | None:
    """The cost recorded on a reply: the priced part of the turn.

    With an unpriced item the total is the known part (``cost_status`` partial);
    it is not posted to the treasury, and that is filed for the owner by
    ``_file_unsettled``. Only a turn with no priced model call has no cost."""
    total = billing.get("total_cost")
    if total is None:
        return None
    return float(total)


def _file_unsettled(settlement: dict[str, Any], **ids: Any) -> None:
    """Every turn the treasury did not charge, except at a zero price, is a Court
    case for the owner (AGENTS.md, «Ошибки и дела Суда»); the user is not told."""
    status = settlement.get("status")
    if status not in {"failed", "skipped"} or settlement.get("reason") == "zero_cost":
        return
    try:
        file_court_case(
            {
                **{key: value for key, value in ids.items() if value is not None},
                "error": {
                    "code": f"treasury_settlement_{status}",
                    "message": f"treasury settlement {status}: {settlement.get('reason')}",
                },
            }
        )
    except Exception:
        logger.exception("[COURT] Settlement case was not filed")


def _settled_turn_cost(
    billing: dict[str, Any], settlement: dict[str, Any]
) -> float | None:
    """The turn's cost, whether or not the treasury could charge it.

    The provider has already billed the tokens, so the price is a fact of the
    turn; a failed posting is reported separately as ``settlement`` and must not
    hide the price (#1664)."""
    return _turn_cost(billing)


def _append_post_final_error(trace_data, source, message, error_type):
    """Record an error raised after ExecutionTrace.finalize() in the returned copy only."""
    import time

    trace_data.setdefault("errors", []).append(
        {
            "source": source,
            "error": message,
            "type": error_type,
            "timestamp": time.time(),
        }
    )


def _provider_conversation_id(client, conv_id, trace):
    """Return the provider conversation behind a local chat, creating it on the first message."""
    mapped = get_yandex_conversation(conv_id)
    if mapped:
        return mapped
    if not is_provider_pending(conv_id):
        # Chats created before this change already use the provider's id as their own id.
        return conv_id
    created = client.create_conversation(execution_trace=trace)
    provider_id = created.get("id") if isinstance(created, dict) else None
    if not provider_id:
        raise RuntimeError("Yandex conversation creation returned no ID")
    # Two first messages can race; the one stored first wins and the other reuses its conversation.
    return set_yandex_conversation(conv_id, provider_id)


def _validate_chat_payload(conv_id, model_key, message, attachments):
    """Validate the turn content before creating an invocation or storing a message."""
    if not isinstance(conv_id, str) or not conv_id.strip():
        return None, None, (jsonify(error_body("conversation_id is required")), 400)
    if not isinstance(model_key, str):
        return None, None, (jsonify(error_body("model must be a string")), 400)
    # These technologies have their own transports; never substitute a text turn.
    endpoint = None
    if (
        model_key in bound.VOICE_MODELS
        or model_key.startswith("speech-realtime-")
        or "/speech-realtime-" in model_key
    ):
        endpoint = "/api/voice/session"
    elif model_key in bound.IMAGE_MODELS or model_key.startswith("art://"):
        endpoint = "/api/images/generate"
    elif model_key in bound.EMBEDDING_MODELS or model_key.startswith("emb://"):
        endpoint = "/api/embeddings"
    elif (
        model_key in bound.CLASSIFIER_MODELS
        or model_key.startswith("cls://")
        or "/classifier-" in model_key
    ):
        endpoint = "/api/classify"
    if endpoint is not None:
        return (
            None,
            None,
            (
                jsonify(
                    error_body("model_requires_dedicated_api", endpoint=endpoint)
                ),
                400,
            ),
        )
    if isinstance(model_key, str) and model_key in completion_model_keys():
        # These models speak the completion API; sending them through Responses is wrong.
        return (
            None,
            None,
            (
                jsonify(
                    error_body(
                        "model_uses_completion_api",
                        endpoint="/api/completion",
                    )
                ),
                400,
            ),
        )
    try:
        images = _validated_images(attachments)
    except ValueError as exc:
        return (
            None,
            None,
            (jsonify(error_body("invalid_attachments", message=str(exc))), 400),
        )
    if message is None and images:
        message = ""
    if not isinstance(message, str) or (not message.strip() and not images):
        return (
            None,
            None,
            (jsonify(error_body("message or attachments are required")), 400),
        )
    if images and model_key not in image_input_model_keys():
        return None, None, (jsonify(error_body("model_does_not_accept_images")), 400)

    return message, images, None


@dataclass(frozen=True)
class _ChatReply:
    reply: Any
    reasoning: Any
    usage: Any
    timings: Any
    provider_diagnostics: dict[str, Any]
    provider_failed: bool
    provider_error: str | None
    provider_message: str | None
    partial_output: Any


def _execute_chat_model(message, model_key, invocation, params, trace):
    """Run one provider/tool turn and collect its terminal response."""
    client = logos_client_type()(bound.Config)
    provider_conversation_id = _provider_conversation_id(
        client, invocation.conversation_id, trace
    )

    if isinstance(trace, bound.ExecutionTrace):
        trace.add_event(
            "turn_limits",
            {
                "poll_timeout": params["poll_timeout"],
                "max_rounds": params["max_rounds"],
            },
        )
    response = client.ask_with_mcp(
        message=message,
        model_key=model_key,
        conversation_id=provider_conversation_id,
        settings_conversation_id=invocation.conversation_id,
        params=params,
        trace=trace,
    )

    api_requests = trace.trace.get("api_requests", [])
    if api_requests:
        first_api_start = api_requests[0].get("timestamp")
        request_init = next(
            (
                event
                for event in trace.trace.get("events", [])
                if event.get("type") == "request_initialized"
            ),
            None,
        )
        request_init_timestamp = request_init.get("timestamp") if request_init else None
        if isinstance(request_init_timestamp, (int, float)) and isinstance(
            first_api_start, (int, float)
        ):
            pre_api_ms = round(
                max(0.0, first_api_start - request_init_timestamp) * 1000, 2
            )
            trace.trace.setdefault("timings", {})["pre_api_pipeline"] = {
                "start_timestamp": request_init_timestamp,
                "end_timestamp": first_api_start,
                "duration_ms": pre_api_ms,
            }
            trace.trace.setdefault("events", []).append(
                {
                    "type": "pre_api_pipeline_completed",
                    "timestamp": first_api_start,
                    "payload": {
                        "start_timestamp": request_init_timestamp,
                        "end_timestamp": first_api_start,
                        "timing_ms": pre_api_ms,
                        "step": api_requests[0].get("step", 1),
                    },
                }
            )

    return _build_chat_reply(client, response, trace)


class _EmptyModelReply(RuntimeError):
    """The provider completed the turn without text; the message is for the user."""


def _build_chat_reply(client, response, trace) -> _ChatReply:
    """Interpret provider completion without publishing history or settling billing."""
    provider_message = None
    invalid_provider_response = not isinstance(response, dict)
    provider_diagnostics = {}
    capture_provider_diagnostics(
        provider_diagnostics, {} if invalid_provider_response else response
    )
    provider_status = provider_diagnostics["provider_status"]
    pending_calls = (
        [] if invalid_provider_response else extract_function_calls(response)
    )
    if invalid_provider_response:
        # Keep the invalid JSON value as evidence too; never convert arbitrary
        # provider data into an assistant reply or replace the last valid raw.
        trace.add_event(
            "provider_invalid_response",
            {"response_type": type(response).__name__, "raw": response},
        )
    provider_failed = (
        provider_status != "completed"
        or provider_diagnostics["provider_error_present"]
        or bool(pending_calls)
    )
    provider_error = None
    if provider_failed:
        provider_error = (
            "provider_invalid_response"
            if invalid_provider_response
            else "provider_tool_calls_unresolved"
            if pending_calls
            else "provider_error"
            if provider_diagnostics["provider_error_present"]
            else "provider_" + provider_status
        )
        if invalid_provider_response:
            provider_message = "Провайдер вернул ответ неверного формата"
        elif pending_calls:
            provider_message = "Вызовы инструментов из ответа провайдера не завершены"
        elif provider_diagnostics["provider_error_present"]:
            provider_message = "Провайдер вернул ошибку"
        else:
            provider_message = "Ответ провайдера не завершён: " + provider_status
        if provider_diagnostics.get("provider_reason"):
            provider_message += " (" + provider_diagnostics["provider_reason"] + ")"
        trace.record_error(
            "provider_response", provider_message, error_type=provider_error
        )
    trace.add_event(
        "provider_terminal_state",
        {
            **provider_diagnostics,
            "invocation_status": "failed" if provider_failed else "completed",
        },
    )

    reasoning, reply = (
        ("", "")
        if invalid_provider_response
        else client.extract_reasoning_and_text(response)
    )
    partial_output = reply if provider_failed else None
    if provider_failed:
        if not partial_output:
            # Only this request's trace is consulted. The final raw response
            # and its status remain untouched even when an earlier tool round
            # supplied the last available assistant text.
            partial_output, _ = extract_last_response_text(
                trace.trace.get("responses", [])
            )
        reply = format_partial_output_message(partial_output, provider_message)
    elif not reply:
        raise _EmptyModelReply(EMPTY_MODEL_REPLY)
    usage = client.extract_usage(response)
    timings = response.get("step_timings", []) if isinstance(response, dict) else []
    return _ChatReply(
        reply=reply,
        reasoning=reasoning,
        usage=usage,
        timings=timings,
        provider_diagnostics=provider_diagnostics,
        provider_failed=provider_failed,
        provider_error=provider_error,
        provider_message=provider_message,
        partial_output=partial_output,
    )


# Global memory: after a completed turn the dialog is read once more for stable facts.
MEMORY_EXTRACTION_PROMPT = (
    "Извлеки из диалога устойчивые факты о проектах, путях, конфигурации и "
    "предпочтениях владельца, которые полезно помнить в других диалогах. Не записывай "
    "пароли, ключи, токены и личные данные. Верни только JSON-объект с полем facts: "
    "список объектов {category, fact}. Если устойчивых фактов нет, верни пустой список."
)
MEMORY_FACTS_SCHEMA = {
    "type": "object",
    "properties": {
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "category": {"type": "string"},
                    "fact": {"type": "string"},
                },
                "required": ["category", "fact"],
            },
        }
    },
    "required": ["facts"],
}


def _remember_turn_facts(owner_id, conv_id, model_key):
    """Save the facts a completed turn's dialog states, off the request thread.

    The extraction is a normal model request: the chat client's credential, quota
    and trace, settled to the owner's treasury like a turn. A failure is logged by
    type only and never changes the reply that was already sent.
    """
    try:
        config = bound.load_memory_config()
        if not owner_id or not config.get("enabled") or not config.get("auto_extraction"):
            return
        lines = [
            f"{message['role']}: {message['text']}"
            for message in bound.get_messages(conv_id)
            if message.get("text")
        ]
        if not lines:
            return
        client = logos_client_type()(bound.Config)
        trace = bound.ExecutionTrace()
        trace.trace.setdefault("context", {})["user_id"] = owner_id
        response = client.ask(
            MEMORY_EXTRACTION_PROMPT + "\n\n" + "\n".join(lines),
            model_key,
            conversation_id=None,
            params={
                "background": False,
                "store": False,
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": "memory_facts",
                        "schema": MEMORY_FACTS_SCHEMA,
                    }
                },
            },
            execution_trace=trace,
        )
        parsed = _json.loads(client.extract_text(response) or "{}")
        facts = parsed.get("facts") if isinstance(parsed, dict) else None
        if isinstance(facts, list):
            bound.save_global_memory_facts(facts)
        billing = trace.finalize().get("billing") or {}
        billing["trace_id"] = "memory-" + uuid.uuid4().hex
        bound.settle_billing_to_treasury(billing, owner_id)
    except Exception as exc:
        logger.warning("[MEMORY] fact extraction failed: %s", type(exc).__name__)


def _finalize_chat_turn(
    invocation,
    trace,
    result: _ChatReply,
    *,
    total_ms,
    selected_skill_metadata,
    conversation_title,
    user_message_id=None,
    model_key=None,
):
    """Freeze the trace, settle billing, persist the reply, then finish its invocation."""
    reply = result.reply
    reasoning = result.reasoning
    usage = result.usage
    timings = result.timings
    provider_diagnostics = result.provider_diagnostics
    provider_failed = result.provider_failed
    provider_error = result.provider_error
    provider_message = result.provider_message
    partial_output = result.partial_output
    trace_data = trace.finalize()

    try:
        settlement = settle_billing_to_treasury(
            trace_data.get("billing") or {},
            invocation.user_id,
        )
    except Exception as settlement_error:
        logger.exception("[BILLING] Treasury settlement failed")
        # The full exception stays in the server log above; the trace gets only fixed text and a known code.
        failure_code = (
            "insufficient_balance"
            if str(settlement_error) == "insufficient balance"
            else None
        )
        settlement_message = "treasury settlement failed" + (
            f": {failure_code}" if failure_code else ""
        )
        _append_post_final_error(
            trace_data,
            "treasury_settlement",
            settlement_message,
            type(settlement_error).__name__,
        )
        settlement = {
            "status": "failed",
            "reason": failure_code or "treasury_settlement_failed",
        }
    # The reply itself succeeded, so fail_invocation never runs for this.
    _file_unsettled(
        settlement,
        invocation_id=invocation.invocation_id,
        conversation_id=invocation.conversation_id,
        session_id=invocation.session_id,
        trace_id=invocation.trace_id,
    )

    trace_data.setdefault("billing", {})["settlement"] = settlement
    billing = trace_data.get("billing") or {}
    cost = _settled_turn_cost(billing, settlement)
    if billing.get("items"):
        # The reply shows the tokens of the whole turn, every model call, the same total its cost is billed on.
        usage = {
            **(usage if isinstance(usage, dict) else {}),
            "input_tokens": billing.get("input_tokens", 0),
            "output_tokens": billing.get("output_tokens", 0),
            "cached_tokens": billing.get("cached_input_tokens", 0),
            "total_tokens": billing.get("total_tokens", 0),
        }

    persist_invocation_trace(invocation.invocation_id, trace_data)
    reply_message_id = add_message(
        invocation.conversation_id,
        "assistant",
        str(reply),
        cost=cost,
        timings=timings,
        usage=usage,
        trace={
            "trace_id": invocation.trace_id,
            "invocation_id": invocation.invocation_id,
            "storage": "invocation",
        },
        total_duration_ms=total_ms,
        source="error" if provider_failed else None,
    )
    outcome = {
        "reply": reply,
        "user_message_id": user_message_id,
        "usage": usage,
        "cost": cost,
        "settlement": settlement,
        **provider_diagnostics,
    }
    if provider_failed:
        fail_invocation(
            invocation.invocation_id,
            error={
                **outcome,
                "code": provider_error,
                "message": provider_message,
                "partial_output": partial_output or None,
            },
        )
    else:
        finish_invocation(invocation.invocation_id, result=outcome)
        if reply and model_key:
            threading.Thread(
                target=_remember_turn_facts,
                args=(invocation.user_id, invocation.conversation_id, model_key),
                daemon=True,
            ).start()

    return jsonify(
        {
            "reply": reply,
            "user_message_id": user_message_id,
            # The UI keys the reply bubble by its stored id.
            "message_id": reply_message_id,
            "usage": usage,
            "cost": cost,
            "settlement": settlement,
            "timings": timings,
            "total_duration_ms": total_ms,
            "reasoning": reasoning,
            "invocation_id": invocation.invocation_id,
            "session_id": invocation.session_id,
            "conversation_id": invocation.conversation_id,
            "trace_id": invocation.trace_id,
            "trace": trace_data,
            "skills": selected_skill_metadata,
            "title": conversation_title
            or get_conversation_title(invocation.conversation_id),
            "status": "failed" if provider_failed else "completed",
            **provider_diagnostics,
            **(
                {"error": provider_error, "partial_output": partial_output or None}
                if provider_failed
                else {}
            ),
        }
    ), 502 if provider_failed else 200


def _turn_error_text(error):
    """Russian text for a failure the user can fix, or None; the exception is logged."""
    if isinstance(error, _EmptyModelReply):
        return str(error)
    if isinstance(error, bound.NoActiveCredentialError):
        return PROVIDER_KEY_MISSING
    if isinstance(error, bound.ExpiredCredentialError):
        return PROVIDER_KEY_EXPIRED
    return None


def _chat_error_response(
    error,
    *,
    trace,
    invocation,
    conv_id,
    can_write,
    error_message,
    is_temperature_error,
    user_message_id=None,
    error_detail=None,
):
    """Publish an error using only the invocation context established by this request.

    ``error_message`` is what the person reads; ``error_detail``, when given, is the
    technical cause kept for the trace and the failed invocation instead.
    """
    recorded_error = error_detail or error_message
    if trace.finalized:
        # The failure happened after the trace was frozen (e.g. while
        # persisting the reply); report it only in the returned copy.
        trace_data = trace.finalize()
        _append_post_final_error(
            trace_data, "chat_pipeline", recorded_error, type(error).__name__
        )
    else:
        if is_temperature_error:
            trace.add_event(
                "validation_failed",
                {
                    "field": "temperature",
                    "error": error_message,
                },
            )
        trace.record_error(
            "chat_pipeline",
            recorded_error,
            error_type=type(error).__name__,
        )
        record_yandex_mcp_activity(trace)
        if isinstance(error, bound.ProviderQuotaExceeded):
            trace.add_event(
                "provider_quota_http_response",
                {
                    "status_code": 429,
                    "reason": error.reason,
                    "user_id": error.user_id,
                },
            )
        trace_data = trace.finalize()
    if isinstance(error, bound.ProviderQuotaExceeded):
        if invocation is not None:
            persist_invocation_trace(invocation.invocation_id, trace_data)
            fail_invocation(invocation.invocation_id, error=error.to_dict()["error"])
        return jsonify(
            {
                **error.to_dict(),
                "reply": "⚠️ Лимит запросов исчерпан. Повторите после указанного времени сброса.",
                "user_message_id": user_message_id,
                "invocation_id": invocation.invocation_id if invocation else None,
                "session_id": invocation.session_id if invocation else None,
                "conversation_id": invocation.conversation_id
                if invocation
                else conv_id,
                "trace_id": invocation.trace_id if invocation else trace.trace_id,
                "trace": trace_data,
            }
        ), 429
    responses = trace_data.get("responses", [])
    partial_output, _ = extract_last_response_text(responses)
    reply = format_partial_output_message(partial_output, error_message)

    # Tokens the provider billed before the failure are a cost of this turn: it is
    # settled like a reply (posting is idempotent, so a failure after an earlier
    # posting finds it already_posted) and stored with the error message.
    cost = None
    if invocation is not None:
        billing = trace_data.get("billing") or {}
        settlement = {"status": "failed", "reason": "treasury_settlement_failed"}
        try:
            settlement = settle_billing_to_treasury(billing, invocation.user_id)
        except Exception as settlement_error:
            logger.exception("[BILLING] Treasury settlement of a failed turn failed")
            if str(settlement_error) == "insufficient balance":
                settlement["reason"] = "insufficient_balance"
        trace_data.setdefault("billing", {})["settlement"] = settlement
        cost = _settled_turn_cost(billing, settlement)
        _file_unsettled(
            settlement,
            invocation_id=invocation.invocation_id,
            conversation_id=invocation.conversation_id,
            session_id=invocation.session_id,
            trace_id=invocation.trace_id,
        )

    if invocation is not None:
        persist_invocation_trace(invocation.invocation_id, trace_data)
    if can_write:
        add_message(
            conv_id,
            "assistant",
            reply,
            cost=cost,
            trace={
                "trace_id": invocation.trace_id,
                "invocation_id": invocation.invocation_id,
                "storage": "invocation",
            }
            if invocation
            else {},
            source="error",
        )
    if invocation is not None:
        fail_invocation(invocation.invocation_id, error={"message": recorded_error})

    return jsonify(
        {
            "error": error_message,
            "reply": reply,
            "cost": cost,
            "user_message_id": user_message_id,
            "partial_output": partial_output if partial_output else None,
            "invocation_id": invocation.invocation_id if invocation else None,
            "session_id": invocation.session_id if invocation else None,
            "conversation_id": invocation.conversation_id if invocation else conv_id,
            "trace_id": invocation.trace_id if invocation else trace.trace_id,
            "trace": trace_data,
        }
    ), 500


@mcp_bp.route("/api/chat", methods=["POST"])
def chat():
    import time as _time

    t_start = _time.perf_counter()
    trace = None
    trace_token = None
    invocation = None
    conv_id = None
    session_id = None
    message = None
    model_key = "yandexgpt-5-pro"
    error_message = None
    user_message_id = None

    try:
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify(error_body("json_body_required")), 400
        conv_id = data.get("conversation_id")
        session_id = data.get("session_id") or conv_id
        message = data.get("message")
        # Only an exact persisted message ID permits omitting a new user record.
        retry = data.get("retry", False)
        if not isinstance(retry, bool):
            return jsonify(
                error_body("invalid_params", message="retry must be a boolean")
            ), 400
        retry_message_id = data.get("retry_message_id")
        if retry_message_id is not None and (
            type(retry_message_id) is not int or retry_message_id < 1 or not retry
        ):
            return jsonify(
                error_body(
                    "invalid_params",
                    message="retry_message_id requires retry and a positive integer",
                )
            ), 400
        raw_params = data.get("params")
        if raw_params is not None and not isinstance(raw_params, dict):
            return jsonify(
                error_body("invalid_params", message="params must be an object")
            ), 400
        params = dict(raw_params or {})
        params_role = params.pop("role", None)
        role = data.get("role") or params_role
        params_model = params.pop("model", "yandexgpt-5-pro")
        model_key = data.get("model") or params_model
        try:
            requested_skills = _normalize_requested_skills(data, params)
            params.pop("skills", None)
            params = _client_params(params)
        except (ValueError, bound.SkillRegistryError) as exc:
            return jsonify(error_body("invalid_params", message=str(exc))), 400

        message, images, invalid_payload = _validate_chat_payload(
            conv_id, model_key, message, data.get("attachments")
        )
        if invalid_payload is not None:
            return invalid_payload

        owner_id = get_current_owner_id(required=False)
        if not check_access(conv_id, owner_id):
            return jsonify(error_body("conversation_not_found")), 404
        conv_settings = get_conv_settings(conv_id) or {}
        if not isinstance(conv_settings, dict):
            return jsonify(
                error_body(
                    "invalid_settings",
                    message="conversation settings must be an object",
                )
            ), 400
        try:
            normalize_tools_config(conv_settings.get("tools_config", {}))
        except ValueError as exc:
            return jsonify(error_body("invalid_settings", message=str(exc))), 400
        if retry and retry_message_id is not None:
            original = get_message(conv_id, retry_message_id)
            if (
                original is None
                or original.get("role") != "user"
                or original.get("content") != (message or "[Фото]")
                or (original.get("attachments") or {}).get("images", 0) != len(images)
            ):
                return jsonify(
                    error_body(
                        "invalid_retry_message",
                        message="The saved user message does not match this retry",
                    )
                ), 409
            user_message_id = retry_message_id
        invocation = create_invocation(
            session_id,
            conv_id,
            metadata={"model": model_key, "role": role, "skills": requested_skills},
            user_id=owner_id,
        )
        trace = create_invocation_trace(invocation)
        trace_token = bind_current_trace(trace)
        start_invocation(invocation.invocation_id)
        trace.set_request(
            {
                "conversation_id": invocation.conversation_id,
                "message": message,
                "model": model_key,
                "params": params,
                "role": role,
                "skills": requested_skills,
                "invocation_id": invocation.invocation_id,
                "session_id": invocation.session_id,
                "trace_id": invocation.trace_id,
            }
        )
        # Added after set_request, so the trace records the request fields without the photos.
        if images:
            params["input_images"] = images

        try:
            selected_skills = skill_registry().load_many(requested_skills, role=role)
        except bound.SkillRegistryError as exc:
            trace.record_error(
                "skills", "invalid skill selection", error_type=type(exc).__name__
            )
            trace_data = trace.finalize()
            persist_invocation_trace(invocation.invocation_id, trace_data)
            public_message = "Selected skills are invalid or unavailable"
            fail_invocation(
                invocation.invocation_id,
                error={"code": "invalid_skill_selection", "message": public_message},
            )
            return jsonify(
                error_body(
                    "invalid_skill_selection",
                    message=public_message,
                    invocation_id=invocation.invocation_id,
                    session_id=invocation.session_id,
                    conversation_id=invocation.conversation_id,
                    trace_id=invocation.trace_id,
                    trace=trace_data,
                )
            ), 400

        for skill in selected_skills:
            trace.add_skill(
                skill.name, source=skill.source, version=skill.version, role=role
            )
        selected_skill_metadata = [skill.metadata() for skill in selected_skills]
        if selected_skills:
            params["instructions"] = compose_skill_instructions(
                selected_skills,
                base_instructions=params.get("instructions"),
            )

        active_tools = conv_settings.get("active_tool_categories")
        if active_tools is not None:
            params["active_tool_categories"] = active_tools

        if user_message_id is None:
            # The photo count is kept with the message, so a reloaded chat still shows which turn had photos.
            user_message_id = add_message(
                invocation.conversation_id,
                "user",
                message or "[Фото]",
                attachments={"images": len(images)} if images else None,
            )
            if user_message_id is None:
                raise LookupError(
                    "Conversation was deleted before the user message was stored"
                )
        conversation_title = maybe_update_conversation_title(
            invocation.conversation_id,
            message,
        )
        result = _execute_chat_model(message, model_key, invocation, params, trace)
        total_ms = round((_time.perf_counter() - t_start) * 1000)
        return _finalize_chat_turn(
            invocation,
            trace,
            result,
            total_ms=total_ms,
            selected_skill_metadata=selected_skill_metadata,
            conversation_title=conversation_title,
            user_message_id=user_message_id,
            model_key=model_key,
        )
    except Exception as e:
        logger.exception("[CHAT] Ошибка")
        internal_error_message = str(e)
        is_temperature_error = (
            isinstance(e, ValueError)
            and "temperature" in internal_error_message.lower()
        )
        turn_error_text = None if is_temperature_error else _turn_error_text(e)
        # An unexpected failure keeps its class and text for the log, the trace («Детали»)
        # and the failed invocation; the reply says what to do in Russian.
        error_detail = (
            None
            if is_temperature_error or turn_error_text
            else f"Внутренняя ошибка обработки запроса ({type(e).__name__}: {internal_error_message[:200]})"
        )
        error_message = (
            "Некорректное значение temperature"
            if is_temperature_error
            else (
                turn_error_text
                or "Сервер не смог обработать запрос. Повторите его; причина записана в трассе: откройте «Детали» под сообщением."
            )
        )

        try:
            # The failure can come before the access check (for example a malformed skills field),
            # so the error path checks the caller itself before it writes to the conversation.
            error_owner_id = get_current_owner_id(required=False)
            can_write = bool(
                conv_id and error_owner_id and check_access(conv_id, error_owner_id)
            )
            if trace is None:
                if can_write:
                    invocation = create_invocation(
                        session_id or conv_id,
                        conv_id,
                        metadata={"model": model_key},
                        user_id=error_owner_id,
                    )
                    trace = create_invocation_trace(invocation)
                    trace_token = bind_current_trace(trace)
                    start_invocation(invocation.invocation_id)
                else:
                    trace = bound.ExecutionTrace()
            return _chat_error_response(
                e,
                trace=trace,
                invocation=invocation,
                conv_id=conv_id,
                can_write=can_write,
                error_message=error_message,
                is_temperature_error=is_temperature_error,
                user_message_id=user_message_id,
                error_detail=error_detail,
            )
        except Exception:
            logger.exception("[CHAT] Не удалось сохранить ExecutionTrace")
            return jsonify(
                {
                    "error": error_message,
                    "reply": f"⚠️ Ошибка: {error_message}",
                    "user_message_id": user_message_id,
                    "partial_output": None,
                    "trace": {},
                }
            ), 500
    finally:
        if trace_token is not None:
            reset_current_trace(trace_token)


@mcp_bp.route("/api/skills", methods=["GET"])
def list_skills():
    role = request.args.get("role")
    try:
        return jsonify({"skills": skill_registry().catalog(role=role)})
    except bound.SkillRegistryError:
        return jsonify(
            error_body(
                "invalid_role",
                message="Role is invalid or unsupported",
            )
        ), 400


@mcp_bp.route("/api/tools/categories", methods=["GET"])
def list_tool_categories():
    return jsonify({"categories": bound.registry.get_available_categories()})


def _tool_category_selection(value: Any) -> list[str] | None:
    """Registered category names only, so a saved selection always maps to real tools."""
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return None
    known = bound.registry.get_available_categories()
    if any(item not in known for item in value):
        return None
    return list(dict.fromkeys(value))


@mcp_bp.route("/api/conversations/<conv_id>/tools", methods=["GET", "PUT"])
def conv_tools(conv_id):
    owner_id = get_current_owner_id(required=False)
    if not check_access(conv_id, owner_id):
        return jsonify(error_body("conversation_not_found")), 404
    if request.method == "PUT":
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            return jsonify(error_body("JSON object is required")), 400
        categories = _tool_category_selection(data.get("active_tool_categories", []))
        if categories is None:
            return jsonify(error_body("invalid_tool_categories")), 400
        # The record is stored as it is applied, so this explicit choice outlives the upgrade.
        settings = upgrade_dialog_settings(get_conv_settings(conv_id) or {})
        settings["active_tool_categories"] = categories
        save_conv_settings(conv_id, settings)
        return jsonify({"status": "ok", "active_tool_categories": categories})

    settings = upgrade_dialog_settings(get_conv_settings(conv_id) or {})
    cats = settings.get("active_tool_categories")
    if cats is None:
        cats = list(bound.registry.get_available_categories().keys())
    return jsonify({"active_tool_categories": cats})


# Stored server credentials are not echoed back to the browser.
# Stored credentials never go back to the browser: an empty value on update means "unchanged".
_MCP_SECRET_FIELDS = ("authorization", "headers")


def _without_secrets(record):
    public = dict(record)
    for field in _MCP_SECRET_FIELDS:
        public["has_" + field] = bool(public.pop(field, ""))
    return public


def _update_payload():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return None
    for field in _MCP_SECRET_FIELDS:
        if not data.get(field):
            data.pop(field, None)
    return data


@mcp_bp.route("/api/mcp-servers", methods=["GET", "POST"])
def handle_mcp_servers():
    owner_id = get_current_owner_id()
    if request.method == "POST":
        data = _update_payload()
        if data is None:
            return jsonify(error_body("JSON object is required")), 400
        try:
            sid = bound.mcp_storage.create_server(owner_id, data)
        except ValueError as exc:
            return jsonify(error_body(str(exc), detail_of="invalid_mcp_server")), 400
        return jsonify({"status": "ok", "id": sid}), 201
    servers = bound.mcp_storage.list_servers(owner_id=owner_id)
    return jsonify({"data": [_without_secrets(srv) for srv in servers]})


@mcp_bp.route("/api/mcp-servers/<server_id>", methods=["GET", "PUT", "DELETE"])
def handle_mcp_server_item(server_id):
    owner_id = get_current_owner_id()
    if request.method == "DELETE":
        bound.mcp_storage.delete_server(server_id, owner_id)
        return jsonify({"status": "deleted"})
    if request.method == "PUT":
        data = _update_payload()
        if data is None:
            return jsonify(error_body("JSON object is required")), 400
        try:
            updated = bound.mcp_storage.update_server(server_id, owner_id, data)
        except ValueError as exc:
            return jsonify(error_body(str(exc), detail_of="invalid_mcp_server")), 400
        if not updated:
            return jsonify(
                error_body("Сервер не найден", detail_of="mcp_server_not_found")
            ), 404
        return jsonify({"status": "updated"})

    srv = bound.mcp_storage.get_server(server_id, owner_id=owner_id)
    if not srv:
        return jsonify(
            error_body("Сервер не найден", detail_of="mcp_server_not_found")
        ), 404
    return jsonify(_without_secrets(srv))


@mcp_bp.route("/api/conversations", methods=["GET", "POST"])
def conversations():
    owner_id = get_current_owner_id()

    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            return jsonify(error_body("JSON object is required")), 400
        try:
            model = validate_dialog_model(data.get("model", "yandexgpt-5-pro"))
        except ValueError:
            return jsonify(error_body("model_not_available_for_dialog")), 400
        except bound.ModelDiscoveryError:
            return jsonify(error_body("model_catalog_unavailable")), 503
        creation_trace = bound.ExecutionTrace()
        creation_trace.set_request(
            {
                "operation": "create_conversation",
                "model": model,
            }
        )
        # A Logos chat gets its local id at once. The provider conversation is created on the first
        # message, so a provider outage can no longer lose the chat itself.
        conv_id = str(uuid.uuid4())
        title = normalize_conversation_title(data.get("title"))
        set_owner(conv_id, owner_id)
        create_conversation(conv_id, title, model, provider_pending=True)
        creation_trace.add_event(
            "conversation_local_persisted",
            {
                "conversation_id": conv_id,
                "model": model,
            },
        )
        return jsonify(
            {
                "id": conv_id,
                "title": title,
                "model": model,
                "trace": creation_trace.finalize(),
            }
        ), 201

    return jsonify({"conversations": list_owned_conversations(owner_id)})


@mcp_bp.route("/api/conversations/<conv_id>/messages", methods=["GET"])
def get_conv_messages(conv_id):
    owner_id = get_current_owner_id(required=False)
    if not check_access(conv_id, owner_id):
        return jsonify(error_body("conversation_not_found")), 404
    return jsonify({"messages": get_messages(conv_id)})


@mcp_bp.route("/api/mcp/execute-approved", methods=["POST"])
def execute_approved():
    try:
        data = request.get_json(silent=True) or {}
        conv_id = data.get("conversation_id")
        func_name = data.get("name")
        arguments = data.get("arguments", {})
        model_key = data.get("model", "yandexgpt-5-pro")

        tool_config = bound.registry.get_tool_meta(func_name)
        if not tool_config:
            return jsonify({"error": f"Неизвестный инструмент: {func_name}"}), 400

        owner_id = get_current_owner_id(required=False)
        if not conv_id or not check_access(str(conv_id), owner_id):
            return jsonify(error_body("conversation_not_found")), 404
        tool_arguments = dict(arguments or {})
        if func_name == "set_ui_theme":
            current_theme = str(data.get("current_theme") or "").strip().lower()
            if (
                current_theme
                and len(current_theme) <= 32
                and all(ch.isalnum() or ch in "-_" for ch in current_theme)
            ):
                tool_arguments["current_theme"] = current_theme
        call = bound.UniversalToolCall(
            tool_name=func_name,
            arguments=tool_arguments,
            transport="internal",
            user_id=owner_id,
            approved=True,
            metadata={"source": "approved_action"},
        )
        exec_res = bound.UniversalToolExecutor(bound.registry).execute(call)
        if not exec_res.get("success"):
            # The tool's own text is always logged. A Russian text from the tool is
            # its own actionable cause and is shown as is; any other text is shown
            # as the generic Russian detail.
            logger.warning(
                "[TOOL_APPROVAL] Tool %s failed (%s): %s",
                func_name,
                (exec_res.get("metadata") or {}).get("phase"),
                exec_res.get("error"),
            )
            tool_error = exec_res.get("error") or "Tool execution failed"
            if re.search("[А-Яа-яЁё]", str(tool_error)):
                failure_body = {
                    "error": tool_error,
                    "detail": str(tool_error),
                    "execution_result": exec_res,
                }
            else:
                failure_body = error_body(
                    tool_error,
                    detail_of="tool_action_failed",
                    execution_result=exec_res,
                )
            return jsonify(failure_body), 403 if (
                (exec_res.get("metadata") or {}).get("phase")
                in {"authorization", "approval_required"}
            ) else 400

        execution_data = exec_res.get("data")
        client = logos_client_type()(bound.Config)
        prompt = (
            f"Пользователь подтвердил действие '{func_name}' с параметрами {arguments}.\n"
            f"Результат: {execution_data}.\nДай краткий ответ о завершении."
        )
        try:
            synth_response = client.ask(
                prompt,
                model_key,
                _provider_conversation_id(client, conv_id, bound.ExecutionTrace()),
                {"instructions": "Ты системный ассистент."},
            )
        except Exception:
            # The action already completed. Preserve its result even if the
            # optional model follow-up fails; never invite re-execution.
            logger.exception("[TOOL_APPROVAL] Follow-up model request failed")
            synth_response = {
                "status": "failed",
                "error": {"code": "approval_followup_failed"},
                "output": [],
            }
        try:
            provider_diagnostics = {}
            capture_provider_diagnostics(
                provider_diagnostics,
                synth_response if isinstance(synth_response, dict) else {},
            )
            followup_reply = (
                client.extract_text(synth_response)
                if isinstance(synth_response, dict)
                else ""
            )
            followup_failed = (
                provider_diagnostics["provider_status"] != "completed"
                or provider_diagnostics["provider_error_present"]
                or bool(extract_function_calls(synth_response))
                or not followup_reply.strip()
            )
            # No usage block (e.g. the follow-up request itself failed) is an unknown
            # cost, not a free turn: extract_usage would report zeros.
            usage = (
                client.extract_usage(synth_response)
                if isinstance(synth_response.get("usage"), dict)
                else None
            )
        except Exception:
            logger.exception("[TOOL_APPROVAL] Invalid follow-up model response")
            provider_diagnostics = {
                "provider_status": "failed",
                "provider_error_present": True,
                "provider_error_code": "approval_followup_invalid_response",
            }
            followup_reply = ""
            followup_failed = True
            usage = None
        if followup_failed:
            # Keep the partial answer the provider did return; dropping it hid the outcome of the action.
            reply = (
                f"Действие {func_name} выполнено. Итоговый ответ модели не завершён. "
                "Повторять действие не нужно."
            )
            if followup_reply.strip():
                reply += "\n\n" + followup_reply.strip()
        else:
            reply = followup_reply
        # The action has already run: a failure in billing or storage below must not report it as failed (#A-008).
        cost = None
        settlement = {"status": "failed", "reason": "approval_billing_failed"}
        try:
            # The follow-up answer is a model call like any chat turn, so it is billed the same way.
            billing = aggregate_billing(
                [build_ai_billing_item(model_key, usage, step=1)],
                context={
                    "trace_id": "approval-" + uuid.uuid4().hex,
                    "owner_id": owner_id,
                    "conversation_id": conv_id,
                },
            )
            cost = _settled_turn_cost(billing, settlement)
            settlement = {"status": "failed", "reason": "treasury_settlement_failed"}
            try:
                settlement = settle_billing_to_treasury(billing, owner_id)
            except Exception as settlement_error:
                logger.exception("[BILLING] Approval follow-up settlement failed")
                if str(settlement_error) == "insufficient balance":
                    settlement["reason"] = "insufficient_balance"
        except Exception:
            logger.exception(
                "[BILLING] Approval follow-up billing failed after the action ran"
            )
        _file_unsettled(settlement, conversation_id=conv_id)
        try:
            add_message(
                conv_id,
                "assistant",
                reply,
                cost=cost,
                source="error" if followup_failed else None,
            )
        except Exception:
            logger.exception(
                "[TOOL_APPROVAL] Could not store the approval reply after the action ran"
            )
        return jsonify(
            {
                "reply": reply,
                "cost": cost,
                "execution_result": exec_res,
                "status": "failed" if followup_failed else "completed",
                **provider_diagnostics,
                **({"error": "approval_followup_failed"} if followup_failed else {}),
            }
        ), 502 if followup_failed else 200
    except Exception:
        logger.exception("[TOOL_APPROVAL] Tool execution failed")
        return jsonify(error_body("tool_execution_failed")), 500

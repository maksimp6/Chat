"""Маршруты чата с сохранением полной цепочки выполнения в БД."""

from flask import Blueprint, request, jsonify
import logging
import re
import json as _json
import uuid
from typing import Any
import base64
from yandex_client_modules.client import YandexResponsesClient
from core.config import TEXT_MODELS, Config
from core.model_profiles import completion_model_keys
from observability.trace_manager import ExecutionTrace, bind_current_trace, reset_current_trace
from invocation.manager import (
    create_invocation,
    start_invocation,
    finish_invocation,
    fail_invocation,
    persist_invocation_trace,
)
from invocation.trace import create_invocation_trace
from mcp_server.trace import record_yandex_mcp_activity
import mcp_server.storage as mcp_storage
from economy.treasury_identity import get_current_owner_id
from tool_providers.registry import registry
from storage.db import (
    create_conversation,
    get_yandex_conversation,
    is_provider_pending,
    set_yandex_conversation,
    get_messages,
    add_message,
    get_conv_settings,
    save_conv_settings,
    maybe_update_conversation_title,
    get_conversation_title,
    normalize_conversation_title,
)
from agent_runtime.partial_output import extract_last_response_text, format_partial_output_message
from agent_runtime.responses_tool_loop import run_tool_loop, extract_function_calls
from economy.billing import aggregate_billing, build_ai_billing_item, settle_billing_to_treasury
from provider_accounts.quotas import ProviderQuotaExceeded
from agent_skills.registry import SkillRegistry, SkillRegistryError, compose_skill_instructions
from tool_providers.universal_tool_platform import (
    UniversalToolCall,
    UniversalToolExecutor,
)
from conversations.ownership import (
    set_owner,
    check_access,
    list_owned_conversations,
)

logger = logging.getLogger("mcp_routes")
mcp_bp = Blueprint("mcp", __name__)
skill_registry = SkillRegistry()


_MAX_TOOL_ROUNDS = 32


def _tool_rounds(value: Any) -> int:
    """Tool-loop rounds the client asked for, clamped so one request cannot loop without bound."""
    try:
        rounds = int(16 if value is None else value)
    except (TypeError, ValueError):
        rounds = 16
    return max(1, min(rounds, _MAX_TOOL_ROUNDS))


def _tool_call_parts(tool_call: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
    function = tool_call.get("function")
    if not isinstance(function, dict):
        function = {}
    name = tool_call.get("name") or function.get("name")
    if name and "<|" in name:
        name = name.split("<|", 1)[0].strip()

    arguments = tool_call.get("arguments") or function.get("arguments", {})
    if isinstance(arguments, str):
        try:
            arguments = _json.loads(arguments)
        except ValueError:
            logger.warning("[CHAT] Tool call arguments are not valid JSON; using empty arguments")
            arguments = {}
    if not isinstance(arguments, dict):
        arguments = {}
    return name, arguments


def _normalize_requested_skills(data, params):
    raw = data.get("skills")
    if raw is None:
        raw = params.pop("skills", [])
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise SkillRegistryError("skills must be a string or a list of strings")
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
        "truncation",
    }
)


def _client_params(raw: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in raw.items() if key in _CLIENT_PARAM_KEYS}


def _record_provider_output(trace, response):
    """What the provider returned for one request: the status, the output kinds and the calls it asked for."""
    if not isinstance(trace, ExecutionTrace) or not isinstance(response, dict):
        return
    output = response.get("output") or []
    trace.add_event(
        "provider_output_observed",
        {
            "status": response.get("status"),
            "output_types": [str(item.get("type"))[:64] for item in output if isinstance(item, dict)][:50],
            "function_calls": [str(call.get("name") or "")[:128] for call in extract_function_calls(response)][:50],
        },
    )


# Models whose registry entry says they read images. A message with photos to any other
# model is refused instead of being sent without them.
IMAGE_INPUT_MODEL_KEYS: frozenset[str] = frozenset(
    key for key, model in TEXT_MODELS.items() if (model.get("capabilities") or {}).get("multimodal") is True
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
        text = _MARKDOWN_PICTURE.sub("[картинка]", str(turn.get("text") or ""))[:IMAGE_HISTORY_TEXT_CHARS]
        if text.strip():
            history.append({"role": turn["role"], "text": text})
    return history


MAX_IMAGES_PER_MESSAGE = 4
MAX_IMAGE_BYTES = 5 * 1024 * 1024


def _has_image_signature(mime: str, image_bytes: bytes) -> bool:
    if mime == "image/png":
        return image_bytes.startswith(b"\x89PNG\r\n\x1a\n")
    if mime == "image/jpeg":
        return image_bytes.startswith(b"\xff\xd8\xff")
    if mime == "image/webp":
        return image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP"
    return False


def _validated_images(raw: Any) -> list[str]:
    """Return the attached images as data URLs, or raise ValueError for a bad attachment."""
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > MAX_IMAGES_PER_MESSAGE:
        raise ValueError(f"attachments must be a list of at most {MAX_IMAGES_PER_MESSAGE} images")
    data_urls = []
    for item in raw:
        url = item.get("data_url") if isinstance(item, dict) else None
        if not isinstance(url, str):
            raise ValueError("each attachment must have a data_url string")
        header, separator, encoded = url.partition(",")
        if not separator or not header.startswith("data:") or not header.endswith(";base64"):
            raise ValueError("each attachment must be a base64 data URL")
        if len(encoded) > MAX_IMAGE_BYTES * 4 // 3 + 4:
            raise ValueError("each image must be at most 5 MB")
        image_bytes = base64.b64decode(encoded, validate=True)
        if len(image_bytes) > MAX_IMAGE_BYTES:
            raise ValueError("each image must be at most 5 MB")
        mime = header[len("data:") : -len(";base64")]
        if not _has_image_signature(mime, image_bytes):
            raise ValueError("image content does not match its type")
        data_urls.append(url)
    return data_urls


class LogosClient(YandexResponsesClient):
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
            {"execution_policy": execution_policy} if execution_policy is not None else {}
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

        if extract_function_calls(response):
            tool_servers = mcp_storage.list_servers(owner_id=get_current_owner_id())
            base_params = dict(params or {})

            def execute_tool(call):
                result = self._execute_single_tool(
                    call, tool_servers, trace=trace, **policy_kwargs
                )
                if result.get("error"):
                    return {"error": result["error"], "tool": result.get("name")}
                return result.get("result")

            def continue_request(input_items, previous_response):
                continuation_params = dict(base_params)
                # A provider conversation stores the answered calls itself, so only the new outputs are sent.
                if conversation_id:
                    continuation_params["input"] = [
                        item for item in input_items
                        if isinstance(item, dict) and item.get("type") == "function_call_output"
                    ]
                else:
                    continuation_params["input"] = input_items
                continuation_params["background"] = base_params.get("background", True)
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

            response = run_tool_loop(
                response,
                execute_tool,
                continue_request,
                max_rounds=_tool_rounds(base_params.get("max_rounds")),
            )

        if trace is not None:
            record_yandex_mcp_activity(trace)
        return response


def _turn_cost(billing: dict[str, Any]) -> float | None:
    """The cost recorded on a reply: the calculated total only. A turn with any unpriced item is
    not posted to the treasury (see settle_billing_to_treasury), so it records no cost either."""
    if billing.get("cost_status") != "calculated":
        return None
    return float(billing.get("total_cost") or 0)


def _append_post_final_error(trace_data, source, message, error_type):
    """Record an error raised after ExecutionTrace.finalize() in the returned copy only."""
    import time

    trace_data.setdefault("errors", []).append(
        {"source": source, "error": message, "type": error_type, "timestamp": time.time()}
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
    set_yandex_conversation(conv_id, provider_id)
    return provider_id


@mcp_bp.route("/api/chat", methods=["POST"])
def chat():
    import time as _time

    t_start = _time.perf_counter()
    trace = None
    trace_token = None
    invocation = None
    trace_data = {}
    conv_id = None
    session_id = None
    message = None
    model_key = "yandexgpt-5-pro"
    partial_output = None
    error_message = None
    selected_skill_metadata = []

    try:
        data = request.get_json(silent=True) or {}
        conv_id = data.get("conversation_id")
        session_id = data.get("session_id") or conv_id
        message = data.get("message")
        # A retry re-runs the last message already stored, so it must not store that message twice.
        retry = bool(data.get("retry"))
        raw_params = data.get("params")
        params = dict(raw_params) if isinstance(raw_params, dict) else {}
        role = data.get("role") or params.pop("role", None)
        requested_skills = _normalize_requested_skills(data, params)
        model_key = data.get("model") or params.get("model", "yandexgpt-5-pro")
        params = _client_params(params)

        if not conv_id or not message:
            return jsonify({"error": "conversation_id и message обязательны"}), 400
        if isinstance(model_key, str) and model_key in completion_model_keys():
            # These models speak the completion API; sending them through Responses is wrong.
            return jsonify({"error": "model_uses_completion_api", "endpoint": "/api/completion"}), 400
        try:
            images = _validated_images(data.get("attachments"))
        except ValueError as exc:
            return jsonify({"error": "invalid_attachments", "message": str(exc)}), 400
        if images and model_key not in IMAGE_INPUT_MODEL_KEYS:
            return jsonify({"error": "model_does_not_accept_images"}), 400

        owner_id = get_current_owner_id(required=False)
        if not check_access(conv_id, owner_id):
            return jsonify({"error": "conversation_not_found"}), 404
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
            selected_skills = skill_registry.load_many(requested_skills, role=role)
        except SkillRegistryError as exc:
            trace.record_error("skills", "invalid skill selection", error_type=type(exc).__name__)
            trace_data = trace.finalize()
            persist_invocation_trace(invocation.invocation_id, trace_data)
            public_message = "Selected skills are invalid or unavailable"
            fail_invocation(
                invocation.invocation_id,
                error={"code": "invalid_skill_selection", "message": public_message},
            )
            return jsonify(
                {
                    "error": "invalid_skill_selection",
                    "message": public_message,
                    "invocation_id": invocation.invocation_id,
                    "session_id": invocation.session_id,
                    "conversation_id": invocation.conversation_id,
                    "trace_id": invocation.trace_id,
                    "trace": trace_data,
                }
            ), 400

        for skill in selected_skills:
            trace.add_skill(skill.name, source=skill.source, version=skill.version, role=role)
        selected_skill_metadata = [skill.metadata() for skill in selected_skills]
        if selected_skills:
            params["instructions"] = compose_skill_instructions(
                selected_skills,
                base_instructions=params.get("instructions"),
            )

        conv_settings = get_conv_settings(invocation.conversation_id) or {}
        active_tools = conv_settings.get("active_tool_categories")
        if active_tools is not None:
            params["active_tool_categories"] = active_tools

        if not retry:
            # The photo count is kept with the message, so a reloaded chat still shows which turn had photos.
            add_message(
                invocation.conversation_id,
                "user",
                message,
                attachments={"images": len(images)} if images else None,
            )
        if images:
            params["chat_history"] = _image_turn_history(invocation.conversation_id)
        conversation_title = maybe_update_conversation_title(
            invocation.conversation_id,
            message,
        )
        client = LogosClient(Config)
        provider_conversation_id = _provider_conversation_id(client, invocation.conversation_id, trace)

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
                pre_api_ms = round(max(0.0, first_api_start - request_init_timestamp) * 1000, 2)
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

        reasoning, reply = client.extract_reasoning_and_text(response)
        if not reply:
            raise RuntimeError("Модель не вернула текстовый ответ. Повторите запрос.")
        usage = client.extract_usage(response)
        timings = response.get("step_timings", []) if isinstance(response, dict) else []
        total_ms = round((_time.perf_counter() - t_start) * 1000)
        trace_data = trace.finalize()

        try:
            settlement = settle_billing_to_treasury(
                trace_data.get("billing") or {},
                invocation.user_id,
            )
        except Exception as settlement_error:
            logger.exception("[BILLING] Treasury settlement failed")
            # The full exception stays in the server log above; the trace gets only fixed text and a known code.
            failure_code = "insufficient_balance" if str(settlement_error) == "insufficient balance" else None
            _append_post_final_error(
                trace_data,
                "treasury_settlement",
                "treasury settlement failed" + (f": {failure_code}" if failure_code else ""),
                type(settlement_error).__name__,
            )
            settlement = {"status": "failed", "reason": "treasury_settlement_failed"}

        trace_data.setdefault("billing", {})["settlement"] = settlement
        billing = trace_data.get("billing") or {}
        cost = _turn_cost(billing)
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
        add_message(
            invocation.conversation_id,
            "assistant",
            str(reply),
            cost=cost,
            timings=timings,
            usage=usage,
            trace={"trace_id": invocation.trace_id, "invocation_id": invocation.invocation_id, "storage": "invocation"},
            total_duration_ms=total_ms,
        )
        finish_invocation(
            invocation.invocation_id,
            result={
                "reply": reply,
                "usage": usage,
                "cost": cost,
                "settlement": settlement,
            },
        )

        return jsonify(
            {
                "reply": reply,
                "usage": usage,
                "cost": cost,
                "timings": timings,
                "total_duration_ms": total_ms,
                "reasoning": reasoning,
                "invocation_id": invocation.invocation_id,
                "session_id": invocation.session_id,
                "conversation_id": invocation.conversation_id,
                "trace_id": invocation.trace_id,
                "trace": trace_data,
                "skills": selected_skill_metadata,
                "title": conversation_title or get_conversation_title(invocation.conversation_id),
            }
        )
    except Exception as e:
        logger.exception("[CHAT] Ошибка")
        internal_error_message = str(e)
        is_temperature_error = (
            isinstance(e, ValueError) and "temperature" in internal_error_message.lower()
        )
        error_message = (
            "Некорректное значение temperature"
            if is_temperature_error
            else f"Внутренняя ошибка обработки запроса ({type(e).__name__}: {internal_error_message[:200]})"
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
                    trace = ExecutionTrace()
            if trace.finalized:
                # The failure happened after the trace was frozen (e.g. while
                # persisting the reply); report it only in the returned copy.
                trace_data = trace.finalize()
                _append_post_final_error(
                    trace_data, "chat_pipeline", error_message, type(e).__name__
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
                    error_message,
                    error_type=type(e).__name__,
                )
                record_yandex_mcp_activity(trace)
                if isinstance(e, ProviderQuotaExceeded):
                    trace.add_event(
                        "provider_quota_http_response",
                        {
                            "status_code": 429,
                            "reason": e.reason,
                            "user_id": e.user_id,
                        },
                    )
                trace_data = trace.finalize()
            if isinstance(e, ProviderQuotaExceeded):
                if invocation is not None:
                    persist_invocation_trace(invocation.invocation_id, trace_data)
                    fail_invocation(invocation.invocation_id, error=e.to_dict()["error"])
                return jsonify(
                    {
                        **e.to_dict(),
                        "reply": "⚠️ Лимит запросов исчерпан. Повторите после указанного времени сброса.",
                        "invocation_id": invocation.invocation_id if invocation else None,
                        "session_id": invocation.session_id if invocation else None,
                        "conversation_id": invocation.conversation_id if invocation else conv_id,
                        "trace_id": invocation.trace_id if invocation else trace.trace_id,
                        "trace": trace_data,
                    }
                ), 429
            responses = trace_data.get("responses", [])
            partial_output, _ = extract_last_response_text(responses)
            reply = format_partial_output_message(partial_output, error_message)

            if invocation is not None:
                persist_invocation_trace(invocation.invocation_id, trace_data)
            if can_write:
                add_message(conv_id, "assistant", reply, trace={"trace_id": invocation.trace_id, "invocation_id": invocation.invocation_id, "storage": "invocation"} if invocation else {}, source="error")
            if invocation is not None:
                fail_invocation(invocation.invocation_id, error={"message": error_message})

            return jsonify(
                {
                    "error": error_message,
                    "reply": reply,
                    "partial_output": partial_output if partial_output else None,
                    "invocation_id": invocation.invocation_id if invocation else None,
                    "session_id": invocation.session_id if invocation else None,
                    "conversation_id": invocation.conversation_id if invocation else conv_id,
                    "trace_id": invocation.trace_id if invocation else trace.trace_id,
                    "trace": trace_data,
                }
            ), 500
        except Exception:
            logger.exception("[CHAT] Не удалось сохранить ExecutionTrace")
            return jsonify(
                {
                    "error": error_message,
                    "reply": f"⚠️ Ошибка: {error_message}",
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
        return jsonify({"skills": skill_registry.catalog(role=role)})
    except SkillRegistryError:
        return jsonify(
            {
                "error": "invalid_role",
                "message": "Role is invalid or unsupported",
            }
        ), 400


@mcp_bp.route("/api/tools/categories", methods=["GET"])
def list_tool_categories():
    return jsonify({"categories": registry.get_available_categories()})


def _tool_category_selection(value: Any) -> list[str] | None:
    """Registered category names only, so a saved selection always maps to real tools."""
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return None
    known = registry.get_available_categories()
    if any(item not in known for item in value):
        return None
    return list(dict.fromkeys(value))


@mcp_bp.route("/api/conversations/<conv_id>/tools", methods=["GET", "PUT"])
def conv_tools(conv_id):
    owner_id = get_current_owner_id(required=False)
    if not check_access(conv_id, owner_id):
        return jsonify({"error": "conversation_not_found"}), 404
    if request.method == "PUT":
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            return jsonify({"error": "JSON object is required"}), 400
        categories = _tool_category_selection(data.get("active_tool_categories", []))
        if categories is None:
            return jsonify({"error": "invalid_tool_categories"}), 400
        settings = get_conv_settings(conv_id) or {}
        settings["active_tool_categories"] = categories
        save_conv_settings(conv_id, settings)
        return jsonify({"status": "ok", "active_tool_categories": categories})

    settings = get_conv_settings(conv_id) or {}
    cats = settings.get("active_tool_categories")
    if cats is None:
        cats = list(registry.get_available_categories().keys())
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
            return jsonify({"error": "JSON object is required"}), 400
        try:
            sid = mcp_storage.create_server(owner_id, data)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"status": "ok", "id": sid}), 201
    servers = mcp_storage.list_servers(owner_id=owner_id)
    return jsonify({"data": [_without_secrets(srv) for srv in servers]})


@mcp_bp.route("/api/mcp-servers/<server_id>", methods=["GET", "PUT", "DELETE"])
def handle_mcp_server_item(server_id):
    owner_id = get_current_owner_id()
    if request.method == "DELETE":
        mcp_storage.delete_server(server_id, owner_id)
        return jsonify({"status": "deleted"})
    if request.method == "PUT":
        data = _update_payload()
        if data is None:
            return jsonify({"error": "JSON object is required"}), 400
        try:
            updated = mcp_storage.update_server(server_id, owner_id, data)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        if not updated:
            return jsonify({"error": "Сервер не найден"}), 404
        return jsonify({"status": "updated"})

    srv = mcp_storage.get_server(server_id, owner_id=owner_id)
    if not srv:
        return jsonify({"error": "Сервер не найден"}), 404
    return jsonify(_without_secrets(srv))


@mcp_bp.route("/api/conversations", methods=["GET", "POST"])
def conversations():
    owner_id = get_current_owner_id()

    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            return jsonify({"error": "JSON object is required"}), 400
        creation_trace = ExecutionTrace()
        creation_trace.set_request(
            {
                "operation": "create_conversation",
                "model": data.get("model", "yandexgpt-5-pro"),
            }
        )
        # A Logos chat gets its local id at once. The provider conversation is created on the first
        # message, so a provider outage can no longer lose the chat itself.
        conv_id = str(uuid.uuid4())
        title = normalize_conversation_title(data.get("title"))
        model = data.get("model", "yandexgpt-5-pro")
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
        return jsonify({"error": "conversation_not_found"}), 404
    return jsonify({"messages": get_messages(conv_id)})


@mcp_bp.route("/api/mcp/execute-approved", methods=["POST"])
def execute_approved():
    try:
        data = request.get_json(silent=True) or {}
        conv_id = data.get("conversation_id")
        func_name = data.get("name")
        arguments = data.get("arguments", {})
        model_key = data.get("model", "yandexgpt-5-pro")

        tool_config = registry.get_tool_meta(func_name)
        if not tool_config:
            return jsonify({"error": f"Неизвестный инструмент: {func_name}"}), 400

        owner_id = get_current_owner_id(required=False)
        if not conv_id or not check_access(str(conv_id), owner_id):
            return jsonify({"error": "conversation_not_found"}), 404
        tool_arguments = dict(arguments or {})
        if func_name == "set_ui_theme":
            current_theme = str(data.get("current_theme") or "").strip().lower()
            if current_theme and len(current_theme) <= 32 and all(
                ch.isalnum() or ch in "-_" for ch in current_theme
            ):
                tool_arguments["current_theme"] = current_theme
        call = UniversalToolCall(
            tool_name=func_name,
            arguments=tool_arguments,
            transport="internal",
            user_id=owner_id,
            approved=True,
            metadata={"source": "approved_action"},
        )
        exec_res = UniversalToolExecutor(registry).execute(call)
        if not exec_res.get("success"):
            return jsonify(
                {
                    "error": exec_res.get("error") or "Tool execution failed",
                    "execution_result": exec_res,
                }
            ), 403 if (exec_res.get("metadata") or {}).get("phase") in {
                "authorization",
                "approval_required",
            } else 400

        execution_data = exec_res.get("data")
        client = LogosClient(Config)
        prompt = (
            f"Пользователь подтвердил действие '{func_name}' с параметрами {arguments}.\n"
            f"Результат: {execution_data}.\nДай краткий ответ о завершении."
        )
        synth_response = client.ask(
            prompt,
            model_key,
            _provider_conversation_id(client, conv_id, ExecutionTrace()),
            {"instructions": "Ты системный ассистент."},
        )
        reply = client.extract_text(synth_response) or f"Действие {func_name} успешно выполнено."
        usage = client.extract_usage(synth_response)
        # The follow-up answer is a model call like any chat turn, so it is billed the same way.
        billing = aggregate_billing(
            [build_ai_billing_item(model_key, usage, step=1)],
            context={
                "trace_id": "approval-" + uuid.uuid4().hex,
                "owner_id": owner_id,
                "conversation_id": conv_id,
            },
        )
        try:
            settle_billing_to_treasury(billing, owner_id)
        except Exception:
            logger.exception("[BILLING] Approval follow-up settlement failed")
        cost = _turn_cost(billing)

        add_message(conv_id, "assistant", reply, cost=cost)
        return jsonify({"reply": reply, "cost": cost, "execution_result": exec_res})
    except Exception:
        logger.exception("[TOOL_APPROVAL] Tool execution failed")
        return jsonify({"error": "tool_execution_failed"}), 500

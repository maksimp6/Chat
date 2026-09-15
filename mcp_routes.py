"""Chat and MCP routes with invocation-scoped execution tracing."""
from flask import Blueprint, request, jsonify
import logging
import uuid
import json as _json
import time as _time

from yandex_client import YandexResponsesClient, YandexMcpMixin
from config import Config, calculate_full_cost
from trace_manager import ExecutionTrace
from trace_context import bind as bind_trace_context, get_trace, clear as clear_trace_context
from invocation_manager import create_invocation, start_invocation, finish_invocation, fail_invocation
from session_manager import create_session, restore_session
from trace_repository import save_trace
import mcp_storage
from tool_registry import registry
from db import get_conversations, create_conversation, get_messages, add_message, get_conv_settings, save_conv_settings
from partial_output import extract_last_response_text, format_partial_output_message

logger = logging.getLogger("mcp_routes")
mcp_bp = Blueprint("mcp", __name__)


class AliceClient(YandexMcpMixin, YandexResponsesClient):
    """Compatibility adapter: the active trace comes from request context, not params."""

    def ask_with_mcp(self, message, model_key, conversation_id=None, params=None):
        return super().ask_with_mcp(
            message=message,
            model_key=model_key,
            conversation_id=conversation_id,
            params=params,
            trace=get_trace(),
        )


def _session_for_conversation(conversation_id: str, requested_session_id: str | None = None) -> str:
    """Resolve a stable session for legacy clients that do not yet send session_id."""
    session_id = requested_session_id or f"conversation:{conversation_id}"
    if not restore_session(session_id):
        create_session(session_id, {"conversation_id": conversation_id, "source": "chat"})
    return session_id


@mcp_bp.route("/api/chat", methods=["POST"])
def chat():
    t_start = _time.perf_counter()
    trace = ExecutionTrace()
    trace_data = {}
    conv_id = None
    invocation_id = None
    error_message = None

    try:
        data = request.get_json(silent=True) or {}
        conv_id = data.get("conversation_id")
        message = data.get("message")
        params = dict(data.get("params") or {})
        model_key = data.get("model") or params.get("model", "aliceai-llm")

        if not conv_id or not message:
            return jsonify({"error": "conversation_id и message обязательны"}), 400

        session_id = _session_for_conversation(conv_id, data.get("session_id"))
        context = create_invocation(
            session_id,
            conv_id,
            {"model": model_key, "source": "/api/chat"},
        )
        invocation_id = context.invocation_id
        trace.trace.update({
            "session_id": context.session_id,
            "conversation_id": context.conversation_id,
            "invocation_id": context.invocation_id,
            "trace_id": context.trace_id,
            "status": "running",
        })
        bind_trace_context(context, trace)
        start_invocation(invocation_id)

        trace.set_request({
            "conversation_id": conv_id,
            "message": message,
            "model": model_key,
            "params": params,
        })

        conv_settings = get_conv_settings(conv_id) or {}
        active_tools = conv_settings.get("active_tool_categories")
        if active_tools is not None:
            params["active_tool_categories"] = active_tools

        add_message(conv_id, "user", message)
        client = AliceClient(Config)
        response = client.ask_with_mcp(
            message=message,
            model_key=model_key,
            conversation_id=conv_id,
            params=params,
        )

        for item in response.get("output", []) if isinstance(response, dict) else []:
            calls = []
            if isinstance(item, dict):
                if item.get("type") in ("function_call", "tool_call"):
                    calls.append(item)
                elif item.get("type") == "message":
                    calls.extend([
                        p for p in item.get("content", [])
                        if isinstance(p, dict) and p.get("type") in ("function_call", "tool_call")
                    ])
            for tc in calls:
                func_name = tc.get("name") or tc.get("function", {}).get("name")
                if func_name and "<|" in func_name:
                    func_name = func_name.split("<|")[0].strip()
                tool_config = registry.get_tool_meta(func_name)
                if tool_config and tool_config.get("requires_approval"):
                    raw_args = tc.get("arguments") or tc.get("function", {}).get("arguments", {})
                    if isinstance(raw_args, str):
                        try:
                            raw_args = _json.loads(raw_args)
                        except Exception:
                            raw_args = {}
                    trace.trace["status"] = "awaiting_approval"
                    trace_data = trace.finalize()
                    save_trace(trace_data)
                    return jsonify({
                        "requires_approval": True,
                        "tool_call": {
                            "name": func_name,
                            "description": tool_config.get("description", func_name),
                            "arguments": raw_args,
                            "call_id": tc.get("call_id") or tc.get("id") or func_name,
                        },
                        "original_message": message,
                        "trace": trace_data,
                    })

        reasoning, reply = client.extract_reasoning_and_text(response)
        reply = reply or "Команда выполнена успешно."
        usage = client.extract_usage(response)
        cost = calculate_full_cost(model_key, usage) if usage else 0.0
        timings = response.get("step_timings", []) if isinstance(response, dict) else []
        total_ms = round((_time.perf_counter() - t_start) * 1000)
        trace.trace["status"] = "completed"
        trace_data = trace.finalize()
        save_trace(trace_data)
        finish_invocation(invocation_id, {"usage": usage, "cost": cost})

        add_message(conv_id, "assistant", str(reply), cost=cost, timings=timings, trace=trace_data)
        return jsonify({
            "reply": reply,
            "usage": usage,
            "cost": cost,
            "timings": timings,
            "total_duration_ms": total_ms,
            "reasoning": reasoning,
            "trace": trace_data,
        })

    except Exception as e:
        logger.exception("[CHAT] Ошибка: %s", e)
        error_message = str(e)
        try:
            trace.record_error("chat_pipeline", error_message, exception=e)
            trace.trace["status"] = "failed"
            trace_data = trace.finalize()
            if invocation_id:
                fail_invocation(invocation_id, {"error": error_message})
            save_trace(trace_data)
            responses = trace_data.get("responses", [])
            partial_output, _ = extract_last_response_text(responses)
            reply = format_partial_output_message(partial_output, error_message)
            if conv_id:
                add_message(conv_id, "assistant", reply, trace=trace_data)
            return jsonify({
                "error": error_message,
                "reply": reply,
                "partial_output": partial_output if partial_output else None,
                "trace": trace_data,
            }), 500
        except Exception as inner_e:
            logger.exception("[CHAT] Не удалось сохранить ExecutionTrace: %s", inner_e)
            return jsonify({
                "error": error_message,
                "reply": f"⚠️ Ошибка: {error_message}",
                "partial_output": None,
                "trace": trace_data or {},
            }), 500
        finally:
            clear_trace_context()
    finally:
        if invocation_id and trace_data.get("status") == "completed":
            clear_trace_context()


@mcp_bp.route("/api/tools/categories", methods=["GET"])
def list_tool_categories():
    return jsonify({"categories": registry.get_available_categories()})


@mcp_bp.route("/api/conversations/<conv_id>/tools", methods=["GET", "PUT"])
def conv_tools(conv_id):
    if request.method == "PUT":
        data = request.get_json(silent=True) or {}
        categories = data.get("active_tool_categories", [])
        settings = get_conv_settings(conv_id) or {}
        settings["active_tool_categories"] = categories
        save_conv_settings(conv_id, settings)
        return jsonify({"status": "ok", "active_tool_categories": categories})
    settings = get_conv_settings(conv_id) or {}
    cats = settings.get("active_tool_categories")
    if cats is None:
        cats = list(registry.get_available_categories().keys())
    return jsonify({"active_tool_categories": cats})


@mcp_bp.route("/api/mcp-servers", methods=["GET", "POST"])
def handle_mcp_servers():
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        sid = mcp_storage.create_server(data)
        return jsonify({"status": "ok", "id": sid}), 201
    return jsonify({"data": mcp_storage.list_servers()})


@mcp_bp.route("/api/mcp-servers/<server_id>", methods=["GET", "PUT", "DELETE"])
def handle_mcp_server_item(server_id):
    if request.method == "DELETE":
        mcp_storage.delete_server(server_id)
        return jsonify({"status": "deleted"})
    if request.method == "PUT":
        data = request.get_json(silent=True) or {}
        mcp_storage.update_server(server_id, data)
        return jsonify({"status": "updated"})
    srv = mcp_storage.get_server(server_id)
    if not srv:
        return jsonify({"error": "Сервер не найден"}), 404
    return jsonify(srv)


@mcp_bp.route("/api/conversations", methods=["GET", "POST"])
def conversations():
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        client = AliceClient(Config)
        try:
            y_conv = client.create_conversation()
            conv_id = y_conv.get("id") or str(uuid.uuid4())
        except Exception:
            conv_id = str(uuid.uuid4())
        title = data.get("title", "Новый диалог")
        model = data.get("model", "aliceai-llm")
        create_conversation(conv_id, title, model)
        return jsonify({"id": conv_id, "title": title, "model": model}), 201
    return jsonify({"conversations": get_conversations()})


@mcp_bp.route("/api/conversations/<conv_id>/messages", methods=["GET"])
def get_conv_messages(conv_id):
    return jsonify({"messages": get_messages(conv_id)})


@mcp_bp.route("/api/mcp/execute-approved", methods=["POST"])
def execute_approved():
    try:
        data = request.get_json(silent=True) or {}
        conv_id = data.get("conversation_id")
        func_name = data.get("name")
        arguments = data.get("arguments", {})
        model_key = data.get("model", "aliceai-llm")
        tool_config = registry.get_tool_meta(func_name)
        if not tool_config:
            return jsonify({"error": f"Неизвестный инструмент: {func_name}"}), 400
        exec_res = registry.execute(func_name, arguments)
        client = AliceClient(Config)
        prompt = (
            f"Пользователь подтвердил действие '{func_name}' с параметрами {arguments}.\n"
            f"Результат: {exec_res}.\nДай краткий ответ о завершении."
        )
        synth_response = client.ask(prompt, model_key, conv_id, {"instructions": "Ты системный ассистент."})
        reply = client.extract_text(synth_response) or f"Действие {func_name} успешно выполнено."
        usage = client.extract_usage(synth_response)
        cost = calculate_full_cost(model_key, usage) if usage else 0.0
        add_message(conv_id, "assistant", reply, cost=cost)
        return jsonify({"reply": reply, "cost": cost, "execution_result": exec_res})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

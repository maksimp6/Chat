"""Маршруты чата с сохранением полной цепочки выполнения в БД."""
from flask import Blueprint, request, jsonify
import logging
import uuid
import json as _json
from yandex_client import YandexResponsesClient, YandexMcpMixin
from config import Config, calculate_full_cost
from trace_manager import ExecutionTrace
import mcp_storage
from tool_registry import registry
from db import (
    get_conversations, create_conversation, get_messages, add_message,
    get_conv_settings, save_conv_settings
)

logger = logging.getLogger("mcp_routes")
mcp_bp = Blueprint('mcp', __name__)

class AliceClient(YandexMcpMixin, YandexResponsesClient):
    pass

@mcp_bp.route('/api/chat', methods=['POST'])
def chat():
    import time as _time
    t_start = _time.perf_counter()
    trace = ExecutionTrace()
    trace_data = {}

    try:
        data = request.get_json(silent=True) or {}
        conv_id = data.get('conversation_id')
        message = data.get('message')
        params = data.get('params', {})
        model_key = data.get('model') or params.get('model', 'aliceai-llm')

        if not conv_id or not message:
            return jsonify({"error": "conversation_id и message обязательны"}), 400

        trace.set_request({
            "conversation_id": conv_id,
            "message": message,
            "model": model_key,
            "params": params
        })

        conv_settings = get_conv_settings(conv_id) or {}
        active_tools = conv_settings.get("active_tool_categories")
        if active_tools is not None:
            params["active_tool_categories"] = active_tools
        params["execution_trace"] = trace

        add_message(conv_id, "user", message)
        client = AliceClient(Config)

        response = client.ask_with_mcp(
            message=message,
            model_key=model_key,
            conversation_id=conv_id,
            params=params
        )

        output = response.get("output", []) if isinstance(response, dict) else []
        for item in output:
            calls = []
            if isinstance(item, dict):
                if item.get("type") in ("function_call", "tool_call"):
                    calls.append(item)
                elif item.get("type") == "message":
                    calls.extend([p for p in item.get("content", []) if isinstance(p, dict) and p.get("type") in ("function_call", "tool_call")])

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

                    trace_data = trace.finalize()
                    return jsonify({
                        "requires_approval": True,
                        "tool_call": {
                            "name": func_name,
                            "description": tool_config.get("description", func_name),
                            "arguments": raw_args,
                            "call_id": tc.get("call_id") or tc.get("id") or func_name
                        },
                        "original_message": message,
                        "trace": trace_data
                    })

        reasoning, reply = client.extract_reasoning_and_text(response)
        if not reply:
            reply = "Команда выполнена успешно."
        usage = client.extract_usage(response)
        cost = calculate_full_cost(model_key, usage) if usage else 0.0
        timings = response.get("step_timings", []) if isinstance(response, dict) else []
        trace_data = response.get("trace", {}) if isinstance(response, dict) else trace.finalize()
        total_ms = round((_time.perf_counter() - t_start) * 1000)

        add_message(conv_id, "assistant", str(reply), cost=cost, timings=timings, trace=trace_data)

        return jsonify({
            "reply": reply,
            "usage": usage,
            "cost": cost,
            "timings": timings,
            "total_duration_ms": total_ms,
            "reasoning": reasoning,
            "trace": trace_data
        })
    except Exception as e:
        logger.exception(f"[CHAT] Ошибка: {e}")
        try:
            trace.record_error("chat_pipeline", str(e), exception=e)
            trace_data = trace.finalize()
            conv_id = locals().get("conv_id")
            if conv_id:
                add_message(
                    conv_id,
                    "assistant",
                    f"Ошибка: {e}",
                    trace=trace_data
                )
        except Exception:
            logger.exception("[CHAT] Не удалось сохранить ExecutionTrace")

        return jsonify({"error": str(e), "trace": trace_data}), 500

@mcp_bp.route('/api/tools/categories', methods=['GET'])
def list_tool_categories():
    return jsonify({
        "categories": registry.get_available_categories()
    })

@mcp_bp.route('/api/conversations/<conv_id>/tools', methods=['GET', 'PUT'])
def conv_tools(conv_id):
    if request.method == 'PUT':
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

@mcp_bp.route('/api/mcp-servers', methods=['GET', 'POST'])
def handle_mcp_servers():
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        sid = mcp_storage.create_server(data)
        return jsonify({"status": "ok", "id": sid}), 201
    return jsonify({"data": mcp_storage.list_servers()})

@mcp_bp.route('/api/mcp-servers/<server_id>', methods=['GET', 'PUT', 'DELETE'])
def handle_mcp_server_item(server_id):
    if request.method == 'DELETE':
        mcp_storage.delete_server(server_id)
        return jsonify({"status": "deleted"})
    if request.method == 'PUT':
        data = request.get_json(silent=True) or {}
        mcp_storage.update_server(server_id, data)
        return jsonify({"status": "updated"})

    srv = mcp_storage.get_server(server_id)
    if not srv:
        return jsonify({"error": "Сервер не найден"}), 404
    return jsonify(srv)

@mcp_bp.route('/api/conversations', methods=['GET', 'POST'])
def conversations():
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        client = AliceClient(Config)
        try:
            y_conv = client.create_conversation()
            conv_id = y_conv.get('id') or str(uuid.uuid4())
        except Exception:
            conv_id = str(uuid.uuid4())
        title = data.get('title', 'Новый диалог')
        model = data.get('model', 'aliceai-llm')
        create_conversation(conv_id, title, model)
        return jsonify({"id": conv_id, "title": title, "model": model}), 201

    return jsonify({"conversations": get_conversations()})

@mcp_bp.route('/api/conversations/<conv_id>/messages', methods=['GET'])
def get_conv_messages(conv_id):
    return jsonify({"messages": get_messages(conv_id)})

@mcp_bp.route('/api/mcp/execute-approved', methods=['POST'])
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

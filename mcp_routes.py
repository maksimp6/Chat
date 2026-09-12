"""Маршруты для чата, Tool Calling и управления MCP-серверами."""
from flask import Blueprint, request, jsonify
import logging
import uuid
import json as _json
from yandex_client import YandexResponsesClient, YandexMcpMixin
from config import Config, calculate_full_cost
import mcp_storage
from db import get_conversations, create_conversation, get_messages, add_message, get_conv_settings

logger = logging.getLogger("mcp_routes")
mcp_bp = Blueprint('mcp', __name__)

class AliceClient(YandexMcpMixin, YandexResponsesClient):
    pass

def find_tool_registry(func_name: str):
    """Ищет метаданные инструмента по всем подключенным модулям"""
    registries = []
    try:
        from git_mcp_tools import TOOL_REGISTRY as GIT_TOOLS
        registries.append(GIT_TOOLS)
    except Exception:
        pass
    try:
        from termux_system_tools import SYSTEM_TOOLS
        registries.append(SYSTEM_TOOLS)
    except Exception:
        pass
    try:
        from filesystem_mcp_tools import FILESYSTEM_TOOLS
        registries.append(FILESYSTEM_TOOLS)
    except Exception:
        pass
    try:
        from termux_mcp_tools import TERMUX_TOOLS
        registries.append(TERMUX_TOOLS)
    except Exception:
        pass
    for reg in registries:
        if func_name in reg:
            return reg[func_name]
    return None

@mcp_bp.route('/api/chat', methods=['POST'])
def chat():
    import time as _time
    t_start = _time.perf_counter()
    try:
        data = request.get_json(silent=True) or {}
        conv_id = data.get('conversation_id')
        message = data.get('message')
        params = data.get('params', {})
        model_key = data.get('model') or params.get('model', 'aliceai-llm')

        if not conv_id or not message:
            return jsonify({"error": "conversation_id и message обязательны"}), 400

        add_message(conv_id, "user", message)
        client = AliceClient(Config)
        logger.info(f"[CHAT] Запрос для диалога {conv_id} (модель {model_key}): {message[:50]}...")

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
                tool_config = find_tool_registry(func_name)

                if tool_config and tool_config.get("requires_approval"):
                    raw_args = tc.get("arguments") or tc.get("function", {}).get("arguments", {})
                    if isinstance(raw_args, str):
                        try:
                            raw_args = _json.loads(raw_args)
                        except Exception:
                            raw_args = {}

                    return jsonify({
                        "requires_approval": True,
                        "tool_call": {
                            "name": func_name,
                            "description": tool_config.get("description", func_name),
                            "arguments": raw_args,
                            "call_id": tc.get("call_id") or tc.get("id") or func_name
                        },
                        "original_message": message
                    })

        reply = client.extract_text(response)
        if not reply or not isinstance(reply, str) or not reply.strip():
            reply = "Команда выполнена успешно."
        usage = client.extract_usage(response)
        cost = calculate_full_cost(model_key, usage) if usage else 0.0

        add_message(conv_id, "assistant", str(reply), cost=cost)
        logger.info(f"[CHAT] Ответ получен. Токены: {usage}, Стоимость: {cost} руб.")

        timings = response.get("step_timings", []) if isinstance(response, dict) else []
        total_ms = round((_time.perf_counter() - t_start) * 1000)
        return jsonify({
            "reply": reply,
            "usage": usage,
            "cost": cost,
            "timings": timings,
            "total_duration_ms": total_ms
        })
    except Exception as e:
        logger.exception(f"[CHAT] Ошибка генерации: {e}")
        return jsonify({"error": str(e)}), 500

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

@mcp_bp.route('/api/mcp/execute-approved', methods=['POST'])
def execute_approved():
    try:
        data = request.get_json(silent=True) or {}
        conv_id = data.get("conversation_id")
        func_name = data.get("name")
        arguments = data.get("arguments", {})
        original_msg = data.get("original_message", "")
        model_key = data.get("model", "aliceai-llm")

        tool_config = find_tool_registry(func_name)
        if not tool_config:
            return jsonify({"error": f"Неизвестный инструмент: {func_name}"}), 400

        exec_res = tool_config["func"](arguments, {})

        client = AliceClient(Config)
        prompt = f"Пользователь подтвердил действие '{func_name}' с параметрами {arguments}.\nРезультат выполнения: {exec_res}.\nДай краткий ответ пользователю о завершении операции."
        synth_response = client.ask(prompt, model_key, conv_id, {"instructions": "Ты системный ассистент. Подтверди выполнение действия."})

        reply = client.extract_text(synth_response) or f"Действие {func_name} успешно выполнено."
        usage = client.extract_usage(synth_response)
        cost = calculate_full_cost(model_key, usage) if usage else 0.0

        add_message(conv_id, "assistant", reply, cost=cost)

        return jsonify({
            "reply": reply,
            "cost": cost,
            "execution_result": exec_res
        })
    except Exception as e:
        logger.exception(f"[MCP APPROVE] Ошибка: {e}")
        return jsonify({"error": str(e)}), 500

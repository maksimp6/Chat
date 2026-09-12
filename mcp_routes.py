"""Маршруты для чата, Tool Calling и управления MCP-серверами."""
from flask import Blueprint, request, jsonify
import logging
import uuid
from yandex_client import YandexResponsesClient, YandexMcpMixin
from config import Config, calculate_full_cost
import mcp_storage
from db import get_conversations, create_conversation, get_messages, add_message, get_conv_settings

logger = logging.getLogger("mcp_routes")
mcp_bp = Blueprint('mcp', __name__)

class AliceClient(YandexMcpMixin, YandexResponsesClient):
    pass

@mcp_bp.route('/api/chat', methods=['POST'])
def chat():
    try:
        data = request.get_json(silent=True) or {}
        conv_id = data.get('conversation_id')
        message = data.get('message')
        params = data.get('params', {})
        model_key = data.get('model') or params.get('model', 'aliceai-llm')

        if not conv_id or not message:
            return jsonify({"error": "conversation_id и message обязательны"}), 400

        # 1. Сохраняем сообщение пользователя
        add_message(conv_id, "user", message)

        # 2. Инициализируем клиент
        client = AliceClient(Config)
        logger.info(f"[CHAT] Запрос для диалога {conv_id} (модель {model_key}): {message[:50]}...")

        # 3. Выполняем цикл с Tool Calling
        response = client.ask_with_mcp(
            message=message,
            model_key=model_key,
            conversation_id=conv_id,
            params=params
        )

        # 4. Извлекаем ответ, токены и рассчитываем реальную стоимость
        reply = client.extract_text(response)
        if not reply or not isinstance(reply, str) or not reply.strip():
            reply = "Команда выполнена успешно."
        usage = client.extract_usage(response)
        cost = calculate_full_cost(model_key, usage) if usage else 0.0

        # 5. Сохраняем ответ ассистента с реальной стоимостью
        add_message(conv_id, "assistant", str(reply), cost=cost)
        logger.info(f"[CHAT] Ответ получен. Токены: {usage}, Стоимость: {cost} руб.")

        timings = response.get("step_timings", []) if isinstance(response, dict) else []
        total_ms = sum(t.get("duration_ms", 0) for t in timings)
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

# === REST API для MCP серверов (для static/settings/settings_mcp.js) ===

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

from flask import Flask, request, jsonify, render_template
import logging
import json
from config import Config, TEXT_MODELS, VOICE_MODELS
from yandex_client import YandexResponsesClient, YandexMcpMixin
from db import (
    init_db, get_conversations, create_conversation, update_conversation_title,
    update_conversation_model, delete_conversation, get_messages, add_message,
    save_conv_settings, get_conv_settings
)
from mcp_routes import mcp_bp
from file_routes import file_bp

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("alice_app")

# Инициализация единого клиента для file_routes и Responses API
class AliceClient(YandexMcpMixin, YandexResponsesClient):
    pass

client = AliceClient(Config)

# Регистрация Blueprint модулей
app.register_blueprint(mcp_bp)
app.register_blueprint(file_bp)

# Инициализация БД
init_db()

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/models", methods=["GET"])
def list_models():
    """Отдает список моделей и тарифов для frontend (core.js, models.js)"""
    return jsonify({
        "text": TEXT_MODELS,
        "voice": VOICE_MODELS
    })

# --- Управление диалогами ---

@app.route("/api/conversations/<conv_id>", methods=["PATCH"])
def patch_conversation(conv_id):
    """Смена заголовка или модели из sidebar.js / core.js"""
    data = request.get_json(silent=True) or {}
    if "title" in data:
        update_conversation_title(conv_id, data["title"])
    if "model" in data:
        update_conversation_model(conv_id, data["model"])
    return jsonify({"status": "ok"})

@app.route("/api/conversations/<conv_id>/title", methods=["PUT"])
def set_conv_title(conv_id):
    data = request.get_json(silent=True) or {}
    title = data.get("title")
    if not title:
        return jsonify({"error": "Title is required"}), 400
    update_conversation_title(conv_id, title)
    return jsonify({"status": "ok", "title": title})

@app.route("/api/conversations/<conv_id>/model", methods=["PUT"])
def set_conv_model(conv_id):
    data = request.get_json(silent=True) or {}
    model = data.get("model")
    if not model:
        return jsonify({"error": "Model is required"}), 400
    update_conversation_model(conv_id, model)
    return jsonify({"status": "ok", "model": model})

@app.route("/api/conversations/<conv_id>", methods=["DELETE"])
def remove_conv(conv_id):
    delete_conversation(conv_id)
    return jsonify({"status": "deleted"})

# --- Настройки диалога ---

@app.route("/api/conversations/<conv_id>/settings", methods=["GET"])
def get_dialog_settings(conv_id):
    settings = get_conv_settings(conv_id)
    return jsonify(settings or {})

@app.route("/api/conversations/<conv_id>/settings", methods=["PUT"])
def save_dialog_settings(conv_id):
    data = request.get_json(silent=True) or {}
    save_conv_settings(conv_id, data)
    return jsonify({"status": "ok"})

# --- Подсистема глобальной памяти ---
from memory_manager import load_memory_config, save_memory_config, clear_global_memory
from db import get_conn
import sqlite3

@app.route("/api/memory/manage", methods=["GET"])
def api_memory_panel_data():
    cfg = load_memory_config()
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM global_memory ORDER BY updated_at DESC")
    facts = [dict(r) for r in cur.fetchall()]
    conn.close()
    return jsonify({"config": cfg, "facts": facts})

@app.route("/api/memory/config", methods=["PUT"])
def api_update_memory_config():
    data = request.get_json(silent=True) or {}
    save_memory_config(data)
    return jsonify({"status": "ok", "config": load_memory_config()})

@app.route("/api/memory/clear", methods=["POST"])
def api_clear_memory():
    data = request.get_json(silent=True) or {}
    category = data.get("category")
    clear_global_memory(category)
    return jsonify({"status": "cleared", "category": category or "all"})


# --- Единый цикл Chat API с ExecutionTrace ---
from trace_manager import ExecutionTrace
from config import calculate_cost

@app.route("/api/chat", methods=["POST"])
def api_chat():
    data = request.get_json(silent=True) or {}
    message_text = data.get("message", "").strip()
    conv_id = data.get("conversation_id")
    params = data.get("params", {})
    model_name = params.get("model") or "aliceai-llm"
    temperature = params.get("temperature", 0.7)

    if not message_text:
        return jsonify({"error": "Message is required"}), 400

    # Создание диалога при необходимости
    if not conv_id:
        conv_res = client.create_conversation()
        conv_id = conv_res.get("id")
        create_conversation(conv_id, message_text[:30], model_name)

    # 1. Сохраняем сообщение пользователя в БД
    add_message(conv_id, "user", message_text)

    # 2. Инициализируем ExecutionTrace
    trace = ExecutionTrace()
    
    # 3. Формируем первоначальный запрос к Yandex Responses API
    req_payload = {
        "model": model_name,
        "conversation": conv_id,
        "message": message_text,
        "temperature": temperature,
        "background": True
    }
    trace.set_request(req_payload)

    final_reply_text = ""
    total_cost = 0.0
    step = 1

    try:
        # Шаг 1. Первый вызов Responses API с передачей trace
        resp_1 = client.ask(
            message=message_text,
            model_key=model_name,
            conversation_id=conv_id,
            params={"temperature": temperature, "background": True},
            execution_trace=trace
        )
        trace.add_response(resp_1, step_index=step)

        # Проверка tool_calls в output
        outputs = resp_1.get("output", [])
        tool_calls = [o for o in outputs if o.get("type") == "function_call"]

        if tool_calls:
            tool_outputs_for_next_turn = []
            for call in tool_calls:
                fn_name = call.get("name")
                raw_args = call.get("arguments", {})
                if isinstance(raw_args, str):
                    try:
                        args = json.loads(raw_args)
                    except Exception:
                        args = {}
                else:
                    args = raw_args

                # Выполнение локального инструмента через trace
                res = trace.track_tool_execution(
                    fn_name,
                    args,
                    client.execute_tool,
                    fn_name,
                    args
                )
                tool_outputs_for_next_turn.append({
                    "name": fn_name,
                    "result": res
                })

            # Шаг 2. Отправка результатов инструментов обратно в модель
            step += 1
            tool_context_msg = "Результат выполнения инструментов:\n" + "\n".join(
                [f"[{t['name']}]: {json.dumps(t['result'], ensure_ascii=False)}" for t in tool_outputs_for_next_turn]
            )
            
            resp_2 = client.ask(
                message=tool_context_msg,
                model_key=model_name,
                conversation_id=conv_id,
                params={"temperature": temperature, "background": True},
                execution_trace=trace
            )
            trace.add_response(resp_2, step_index=step)
            final_reply_text = client.extract_text(resp_2)
            usage = resp_2.get("usage") or {}
        else:
            final_reply_text = client.extract_text(resp_1)
            usage = resp_1.get("usage") or {}

        # Расчет стоимости
        in_tokens = usage.get("input_tokens", 0)
        out_tokens = usage.get("output_tokens", 0)
        total_cost = calculate_cost(model_name, in_tokens, out_tokens)

    except Exception as exc:
        logger.exception("Ошибка при обработке /api/chat")
        trace.record_error("chat_pipeline", str(exc))
        final_reply_text = f"Произошла ошибка при генерации ответа: {str(exc)}"

    # Финализируем trace на ВСЕХ ветках (нормальное завершение или ошибка)
    final_trace_dict = trace.finalize()

    # 4. Сохраняем сообщение ассистента вместе с trace_json в БД
    add_message(
        conv_id=conv_id,
        role="assistant",
        content=final_reply_text,
        cost=total_cost,
        trace=final_trace_dict
    )

    # 5. Возвращаем клиенту ответ и изолированный трейс
    # BACKWARD COMPATIBLE: сохраняем существующие ключи (reply, usage, timings, cost)
    return jsonify({
        "reply": final_reply_text,
        "conversation_id": conv_id,
        "trace": final_trace_dict
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True, use_reloader=False)

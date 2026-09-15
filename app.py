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
from runtime_api import runtime_bp
from runtime_migrations import init_runtime_tables

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
app.register_blueprint(runtime_bp)

# Инициализация БД
init_db()
init_runtime_tables()

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

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True, use_reloader=False)

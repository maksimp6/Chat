from flask import Flask, request, jsonify, render_template
import logging
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

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True, use_reloader=False)

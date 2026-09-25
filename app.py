from flask import Flask, request, jsonify, render_template
import os
import logging
import json
from config import TEXT_MODELS, VOICE_MODELS
from model_discovery import ModelDiscoveryError, get_model_discovery, static_model_catalog
from db import (
    init_db, get_conversations, create_conversation, update_conversation_title,
    update_conversation_model, delete_conversation, get_messages, add_message,
    save_conv_settings, get_conv_settings
)
from mcp_routes import mcp_bp
from chatgpt_mcp import chatgpt_mcp_bp
from file_routes import file_bp
from runtime_api import runtime_bp
from runtime_migrations import init_runtime_tables
from local_agent_gateway import local_agent_bp, init_local_agent_tables
from cloudru_iam_routes import cloudru_iam_bp
from provider_credentials_routes import provider_credentials_bp
from partner_relations import partner_relations_bp, init_partner_relations_tables, ensure_partner_department
from supabase_startup_check import check_supabase_trace_mirror
from treasury import init_treasury_tables, get_account, demo_top_up
from treasury_identity import TreasuryIdentityError, get_current_owner_id
from user_identity import init_user_identity_table, register_anonymous_user
from departments import departments_bp, init_department_tables
from short_token_auth import install_short_token_auth
from conversation_ownership import init_conversation_ownership_table, check_access, delete_owner
from ssh_runtime_settings import public_settings, save_settings, test_connection

app = Flask(__name__)
logging.basicConfig(level=logging.DEBUG)
install_short_token_auth(app)


def preview_base_path():
    """Return the configured URL prefix used by a preview deployment."""
    return os.environ.get("ALICE_PREVIEW_BASE_PATH", "").rstrip("/")


def _static_asset_version():
    """Return a stable version token that changes when the deployed assets change."""
    configured = os.environ.get("ALICE_STATIC_VERSION")
    if configured:
        return configured
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    try:
        mtimes = []
        for root, _, files in os.walk(static_dir):
            for name in files:
                if name.endswith((".js", ".css", ".svg", ".png", ".woff", ".woff2")):
                    mtimes.append(os.stat(os.path.join(root, name)).st_mtime_ns)
        return str(max(mtimes)) if mtimes else "1"
    except OSError:
        return "1"


STATIC_ASSET_VERSION = _static_asset_version()

logger = logging.getLogger("alice_app")

app.register_blueprint(mcp_bp)
app.register_blueprint(chatgpt_mcp_bp)
app.register_blueprint(file_bp)
app.register_blueprint(runtime_bp)
app.register_blueprint(local_agent_bp)
app.register_blueprint(cloudru_iam_bp)
app.register_blueprint(provider_credentials_bp)
app.register_blueprint(partner_relations_bp)
app.register_blueprint(departments_bp)

@app.after_request

def _set_web_cache_headers(response):
    # The HTML shell must never pin an older JavaScript dependency graph across deploys.
    if request.path == "/":
        response.headers["Cache-Control"] = "no-store, max-age=0"
    elif request.path.startswith("/static/"):
        response.headers.setdefault("Cache-Control", "no-cache")
    return response


init_db()
init_runtime_tables()
init_local_agent_tables()
check_supabase_trace_mirror()
init_treasury_tables()
init_user_identity_table()
init_conversation_ownership_table()
init_department_tables()
init_partner_relations_tables()
ensure_partner_department()


@app.route("/")
def index():
    return render_template("index.html", preview_base_path=preview_base_path(), static_version=STATIC_ASSET_VERSION)


@app.route("/healthz", methods=["GET"])
def healthz():
    return jsonify({"status": "ok"})


@app.route("/api/users/bootstrap", methods=["POST"])
def bootstrap_anonymous_user():
    data = request.get_json(silent=True) or {}
    metadata = data.get("metadata") or {}
    if not isinstance(metadata, dict):
        return jsonify({"error": "metadata must be an object"}), 400

    try:
        identity = register_anonymous_user(data.get("installation_id"), metadata)
        response = jsonify(identity)
        response.set_cookie(
            "alice_user_token",
            identity["auth_token"],
            httponly=True,
            secure=request.is_secure,
            samesite="Lax",
            path="/",
        )
        return response
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


MODEL_DISCOVERY = get_model_discovery()


@app.route("/api/models", methods=["GET"])
def list_models():
    force_refresh = request.args.get("refresh", "").lower() in {"1", "true", "yes"}
    try:
        catalog = MODEL_DISCOVERY.get_models(force_refresh=force_refresh)
        catalog["degraded"] = False
        return jsonify(catalog)
    except ModelDiscoveryError:
        catalog = static_model_catalog()
        catalog["degraded"] = True
        catalog["error"] = "model_discovery_unavailable"
        return jsonify(catalog)


@app.route("/api/conversations/<conv_id>", methods=["PATCH"])
def patch_conversation(conv_id):
    owner_id = get_current_owner_id(required=False)
    if owner_id and not check_access(conv_id, owner_id):
        return jsonify({"error": "conversation_not_found"}), 404
    data = request.get_json(silent=True) or {}
    if "title" in data:
        update_conversation_title(conv_id, data["title"], owner_id)
    if "model" in data:
        update_conversation_model(conv_id, data["model"])
    return jsonify({"status": "ok"})


@app.route("/api/conversations/<conv_id>/title", methods=["PUT"])
def set_conv_title(conv_id):
    owner_id = get_current_owner_id(required=False)
    if owner_id and not check_access(conv_id, owner_id):
        return jsonify({"error": "conversation_not_found"}), 404
    title = (request.get_json(silent=True) or {}).get("title")
    if not title:
        return jsonify({"error": "Title is required"}), 400
    update_conversation_title(conv_id, title, owner_id)
    return jsonify({"status": "ok", "title": title})


@app.route("/api/conversations/<conv_id>/model", methods=["PUT"])
def set_conv_model(conv_id):
    owner_id = get_current_owner_id(required=False)
    if owner_id and not check_access(conv_id, owner_id):
        return jsonify({"error": "conversation_not_found"}), 404
    model = (request.get_json(silent=True) or {}).get("model")
    if not model:
        return jsonify({"error": "Model is required"}), 400
    update_conversation_model(conv_id, model, owner_id)
    return jsonify({"status": "ok", "model": model})


@app.route("/api/conversations/<conv_id>", methods=["DELETE"])
def remove_conv(conv_id):
    owner_id = get_current_owner_id(required=False)
    if owner_id and not check_access(conv_id, owner_id):
        return jsonify({"error": "conversation_not_found"}), 404
    delete_conversation(conv_id)
    delete_owner(conv_id, owner_id)
    return jsonify({"status": "deleted"})


@app.route("/api/conversations/<conv_id>/settings", methods=["GET"])
def get_dialog_settings(conv_id):
    return jsonify(get_conv_settings(conv_id) or {})


@app.route("/api/conversations/<conv_id>/settings", methods=["PUT"])
def save_dialog_settings(conv_id):
    save_conv_settings(conv_id, request.get_json(silent=True) or {})
    return jsonify({"status": "ok"})


from memory_manager import load_memory_config, save_memory_config, clear_global_memory
from db import get_conn
import sqlite3


@app.route("/api/memory/manage", methods=["GET"])
def api_memory_panel_data():
    cfg = load_memory_config()
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    facts = [dict(r) for r in conn.execute("SELECT * FROM global_memory ORDER BY updated_at DESC").fetchall()]
    conn.close()
    return jsonify({"config": cfg, "facts": facts})


@app.route("/api/memory/config", methods=["PUT"])
def api_update_memory_config():
    data = request.get_json(silent=True) or {}
    save_memory_config(data)
    return jsonify({"status": "ok", "config": load_memory_config()})


@app.route("/api/memory/clear", methods=["POST"])
def api_clear_memory():
    category = (request.get_json(silent=True) or {}).get("category")
    clear_global_memory(category)
    return jsonify({"status": "cleared", "category": category or "all"})


@app.route("/api/treasury/account", methods=["GET"])
def treasury_account():
    try:
        return jsonify(get_account(get_current_owner_id()))
    except TreasuryIdentityError as exc:
        return jsonify({"error": str(exc)}), 401


@app.route("/api/treasury/top-up", methods=["POST"])
def treasury_top_up():
    data = request.get_json(silent=True) or {}
    try:
        owner_id = get_current_owner_id()
        account = demo_top_up(owner_id, data.get("amount"), data.get("description", "Demo top-up"))
        return jsonify({"demo": True, "account": account}), 201
    except TreasuryIdentityError as exc:
        return jsonify({"error": str(exc)}), 401
    except (TypeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/ssh-runtime/settings", methods=["GET"])
def get_ssh_runtime_settings():
    try:
        get_current_owner_id()
        return jsonify(public_settings())
    except TreasuryIdentityError as exc:
        return jsonify({"error": str(exc)}), 401
    except Exception as exc:
        logger.exception("Failed to load SSH Runtime settings")
        return jsonify({"error": str(exc)}), 400


@app.route("/api/ssh-runtime/settings", methods=["PUT"])
def put_ssh_runtime_settings():
    try:
        get_current_owner_id()
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            return jsonify({"error": "settings must be an object"}), 400
        saved = save_settings(data)
        return jsonify({"status": "ok", "settings": saved})
    except TreasuryIdentityError as exc:
        return jsonify({"error": str(exc)}), 401
    except Exception as exc:
        logger.exception("Failed to save SSH Runtime settings")
        return jsonify({"error": str(exc)}), 400


@app.route("/api/ssh-runtime/test", methods=["POST"])
def test_ssh_runtime_connection():
    try:
        owner_id = get_current_owner_id()
        data = request.get_json(silent=True) or {}
        target = str(data.get("target") or "").strip()
        if not target:
            return jsonify({"error": "target is required"}), 400
        result = test_connection(target, owner_id)
        return jsonify(result)
    except TreasuryIdentityError as exc:
        return jsonify({"error": str(exc)}), 401
    except Exception as exc:
        logger.exception("SSH Runtime connection test failed")
        return jsonify({"success": False, "error": str(exc)}), 400


if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "5000"))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host=host, port=port, debug=debug, use_reloader=False)

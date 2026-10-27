from core.env import env_get
from flask import Flask, request, jsonify, render_template
from werkzeug.exceptions import HTTPException
from kristall import KristallError
import os
import logging
import json
import hashlib
import re
import subprocess
from pathlib import Path
from datetime import datetime, timezone
from core.config import TEXT_MODELS, VOICE_MODELS, get_donation_url
from core.model_profiles import annotate_catalog
from yandex_client_modules.tool_config import normalize_tools_config
from provider_accounts.model_discovery import (
    ModelDiscoveryError,
    get_model_discovery,
    static_model_catalog,
)
from storage.db import (
    init_db,
    memory_file_path,
    create_conversation,
    update_conversation_title,
    update_conversation_model,
    delete_conversation,
    get_messages,
    add_message,
    save_conv_settings,
    get_conv_settings,
    get_store,
)
from chat.mcp_routes import mcp_bp
from mcp_server import chatgpt_mcp_bp
from workspace.file_routes import file_bp
from runtime.api import runtime_bp
from runtime import current_runtime_base_path
from agent_runtime.local_gateway import local_agent_bp, init_local_agent_tables
from cloud.cloudru.iam_routes import cloudru_iam_bp
from provider_accounts.credentials_routes import provider_credentials_bp
from provider_accounts.quota_routes import provider_quota_bp
from organization.partner_relations import (
    partner_relations_bp,
    init_partner_relations_tables,
    ensure_partner_department,
)
from economy.treasury import init_treasury_tables, get_account, demo_top_up
from economy.treasury_identity import TreasuryIdentityError, get_current_owner_id
from organization.departments import departments_bp, init_department_tables
from organization.government import (
    government_bp,
    init_government_tables,
    ensure_government_department,
)
from remote_runtime.environment_routes import environment_bp, environment_gateway_bp
from remote_runtime.environments import init_environment_tables
from rdc_connection.pairing import install_rdc_pairing
from invocation.problems import problem
from observability import system_log
from conversations.ownership import (
    init_conversation_ownership_table,
    check_access,
    delete_owner,
    get_owned_conversation,
)
from remote_runtime.ssh_settings import public_settings, save_settings, test_connection
from plugin_system.routes import plugin_bp
from workspace.project_tree import project_tree_bp
from chat.voice_routes import voice_bp
from chat.image_routes import image_bp
from chat.embedding_routes import embedding_bp
from chat.classification_routes import classification_bp
from chat.completion_routes import completion_bp
from forge import init_printing_tables
from forge.routes import printing3d_bp

app = Flask(__name__)
system_log.configure()
install_rdc_pairing(app)


def preview_base_path():
    """Return the request-scoped preview prefix, falling back to deployment config."""
    scoped = current_runtime_base_path()
    if scoped:
        return scoped
    return env_get("LOGOS_PREVIEW_BASE_PATH", "").rstrip("/")


def _static_asset_version(root=None):
    """Fingerprint the served UI bytes, independently of file timestamps or overrides."""
    root = Path(root) if root is not None else Path(__file__).resolve().parent
    paths = sorted(
        path for path in (root / "static").rglob("*")
        if path.is_file() and path.suffix in {".js", ".css", ".svg", ".png", ".woff", ".woff2"}
    )
    paths.extend(sorted((root / "templates").rglob("*.html")))
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _runtime_revision():
    configured = str(env_get("LOGOS_APP_REVISION") or "").strip()
    if re.fullmatch(r"[0-9a-f]{40}", configured):
        return configured
    try:
        result = subprocess.run(
            ["git", "-C", str(Path(__file__).resolve().parent), "rev-parse", "--verify", "HEAD"],
            capture_output=True, text=True, timeout=2, check=False,
        )
        value = result.stdout.strip()
        if result.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", value):
            return value
    except (OSError, subprocess.SubprocessError):
        pass
    return "unknown"


STATIC_ASSET_VERSION = _static_asset_version()
APP_VERSION = {
    "runtime_revision": _runtime_revision(),
    "ui_version": STATIC_ASSET_VERSION,
    "process_id": os.getpid(),
    "started_at": datetime.now(timezone.utc).isoformat(),
}

logger = system_log.get_logger("app")
logger.info("[BOOT] hello: Logos backend module loaded")

app.register_blueprint(mcp_bp)
app.register_blueprint(chatgpt_mcp_bp)
app.register_blueprint(file_bp)
app.register_blueprint(runtime_bp)
app.register_blueprint(local_agent_bp)
app.register_blueprint(cloudru_iam_bp)
app.register_blueprint(provider_credentials_bp)
app.register_blueprint(provider_quota_bp)
app.register_blueprint(partner_relations_bp)
app.register_blueprint(departments_bp)
app.register_blueprint(government_bp)
app.register_blueprint(environment_bp)
app.register_blueprint(environment_gateway_bp)
app.register_blueprint(plugin_bp)
app.register_blueprint(project_tree_bp)
app.register_blueprint(voice_bp)
app.register_blueprint(image_bp)
app.register_blueprint(classification_bp)
app.register_blueprint(embedding_bp)
app.register_blueprint(completion_bp)
app.register_blueprint(printing3d_bp)


@app.errorhandler(Exception)
def _handle_unexpected_error(exc):
    if isinstance(exc, HTTPException):
        return exc.get_response()
    logger.exception("[ERROR] Unhandled application exception")
    return problem(
        500,
        "internal_server_error",
        "Внутренняя ошибка сервера",
        extensions={"code": "UNHANDLED_EXCEPTION"},
    )


@app.after_request
def _set_web_cache_headers(response):
    # The HTML shell must never pin an older JavaScript dependency graph across deploys.
    if request.path == "/":
        response.headers["Cache-Control"] = "no-store, max-age=0"
    elif request.path.startswith("/static/"):
        response.headers.setdefault("Cache-Control", "no-cache")
    elif request.path.startswith("/api/"):
        # API responses may contain user-specific state, traces, settings, or credentials.
        response.headers["Cache-Control"] = "no-store"
    return response


_storage_ready = False


def _initialize_storage() -> None:
    """Create durable tables once; runs at import when the memory path is configured."""
    global _storage_ready
    init_db()
    init_local_agent_tables()
    init_treasury_tables()
    init_printing_tables()
    init_conversation_ownership_table()
    init_department_tables()
    from provider_accounts.quotas import init_quota_tables

    init_quota_tables()
    init_partner_relations_tables()
    ensure_partner_department()
    init_government_tables()
    ensure_government_department()
    init_environment_tables()
    _storage_ready = True
    logger.info("[BOOT] hello: database initialization completed")


def _memory_path_configured() -> bool:
    try:
        memory_file_path()
    except KristallError:
        return False
    return True


if _memory_path_configured():
    _initialize_storage()


@app.before_request
def _initialize_storage_on_first_request():
    # Without a configured memory path the app still imports; the first data request fails.
    if not _storage_ready and request.path not in {"/healthz", "/api/version"}:
        _initialize_storage()


@app.route("/")
def index():
    # Messages are rendered only by the client (static/chat.js loadHistory).
    return render_template(
        "index.html",
        preview_base_path=preview_base_path(),
        static_version=STATIC_ASSET_VERSION,
        app_revision=APP_VERSION["runtime_revision"],
        donation_url=get_donation_url(),
    )


@app.route("/healthz", methods=["GET"])
def healthz():
    """Process liveness only; it does not certify chat persistence or model access."""
    return jsonify({"status": "ok", "scope": "process", "chat_verified": False})


@app.route("/readyz", methods=["GET"])
def readyz():
    """Read-only storage readiness; a successful read is not a chat E2E proof."""
    try:
        if not _storage_ready:
            _initialize_storage()
        get_store().get("chat/next-message-id")
    except Exception:
        app.logger.exception("Storage readiness failed")
        return jsonify({"status": "unavailable", "scope": "storage_read"}), 503
    return jsonify({"status": "ok", "scope": "storage_read", "chat_write_verified": False})


@app.route("/api/version", methods=["GET"])
def loaded_version():
    """Return the version captured by this process at boot, never a later checkout."""
    return jsonify(dict(APP_VERSION))


@app.route("/api/users/bootstrap", methods=["POST"])
def bootstrap_local_user():
    """Compatibility endpoint for older clients; no registration or token is needed."""
    return jsonify({"user_id": get_current_owner_id(), "status": "local"})


MODEL_DISCOVERY = get_model_discovery()


@app.route("/api/models", methods=["GET"])
def list_models():
    force_refresh = request.args.get("refresh", "").lower() in {"1", "true", "yes"}
    try:
        catalog = annotate_catalog(MODEL_DISCOVERY.get_models(force_refresh=force_refresh))
        catalog["degraded"] = False
        return jsonify(catalog)
    except ModelDiscoveryError:
        catalog = annotate_catalog(static_model_catalog())
        catalog["degraded"] = True
        catalog["error"] = "model_discovery_unavailable"
        return jsonify(catalog)


@app.route("/api/conversations/<conv_id>", methods=["PATCH"])
def patch_conversation(conv_id):
    owner_id = get_current_owner_id(required=False)
    if not owner_id or not check_access(conv_id, owner_id):
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
    if not owner_id or not check_access(conv_id, owner_id):
        return jsonify({"error": "conversation_not_found"}), 404
    title = (request.get_json(silent=True) or {}).get("title")
    if not title:
        return jsonify({"error": "Title is required"}), 400
    update_conversation_title(conv_id, title, owner_id)
    return jsonify({"status": "ok", "title": title})


@app.route("/api/conversations/<conv_id>/model", methods=["PUT"])
def set_conv_model(conv_id):
    owner_id = get_current_owner_id(required=False)
    if not owner_id or not check_access(conv_id, owner_id):
        return jsonify({"error": "conversation_not_found"}), 404
    model = (request.get_json(silent=True) or {}).get("model")
    if not model:
        return jsonify({"error": "Model is required"}), 400
    update_conversation_model(conv_id, model)
    return jsonify({"status": "ok", "model": model})


@app.route("/api/conversations/<conv_id>", methods=["DELETE"])
def remove_conv(conv_id):
    owner_id = get_current_owner_id(required=False)
    if not owner_id or not check_access(conv_id, owner_id):
        return jsonify({"error": "conversation_not_found"}), 404
    delete_conversation(conv_id)
    delete_owner(conv_id, owner_id)
    return jsonify({"status": "deleted"})


@app.route("/api/conversations/<conv_id>/settings", methods=["GET"])
def get_dialog_settings(conv_id):
    owner_id = get_current_owner_id(required=False)
    if not owner_id or not check_access(conv_id, owner_id):
        return jsonify({"error": "conversation_not_found"}), 404
    return jsonify(get_conv_settings(conv_id) or {})


@app.route("/api/conversations/<conv_id>/settings", methods=["PUT"])
def save_dialog_settings(conv_id):
    owner_id = get_current_owner_id(required=False)
    if not owner_id or not check_access(conv_id, owner_id):
        return jsonify({"error": "conversation_not_found"}), 404
    settings = request.get_json(silent=True)
    if not isinstance(settings, dict):
        return jsonify({"error": "JSON object is required"}), 400
    if "tools_config" in settings:
        try:
            settings["tools_config"] = normalize_tools_config(settings["tools_config"])
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
    save_conv_settings(conv_id, settings)
    return jsonify({"status": "ok"})


from agent_memory.manager import load_memory_config, save_memory_config, clear_global_memory
from agent_memory.extractor import list_global_memory_facts


@app.route("/api/memory/manage", methods=["GET"])
def api_memory_panel_data():
    cfg = load_memory_config()
    facts = list_global_memory_facts()
    return jsonify({"config": cfg, "facts": facts})


@app.route("/api/memory/config", methods=["PUT"])
def api_update_memory_config():
    data = request.get_json(silent=True) or {}
    try:
        save_memory_config(data)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"status": "ok", "config": load_memory_config()})


@app.route("/api/memory/clear", methods=["POST"])
def api_clear_memory():
    category = (request.get_json(silent=True) or {}).get("category")
    if category is not None and not isinstance(category, str):
        return jsonify({"error": "category must be a string"}), 400
    clear_global_memory(category)
    return jsonify({"status": "cleared", "category": category or "all"})


@app.route("/api/treasury/account", methods=["GET"])
def treasury_account():
    owner_id = str(request.args.get("owner_id") or get_current_owner_id())
    return jsonify(get_account(owner_id))


@app.route("/api/treasury/accounts", methods=["GET"])
def treasury_accounts():
    """Keep all pre-existing ledgers discoverable without combining their balances."""
    return jsonify({"accounts": list(get_store().items(prefix="treasury/account/").values())})


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
        return jsonify(public_settings())
    except TreasuryIdentityError as exc:
        return jsonify({"error": str(exc)}), 401
    except Exception as exc:
        logger.exception("Failed to load SSH Runtime settings")
        return jsonify({"error": str(exc)}), 400


@app.route("/api/ssh-runtime/settings", methods=["PUT"])
def put_ssh_runtime_settings():
    try:
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
    except Exception:
        logger.exception("SSH Runtime connection test failed")
        return jsonify({"success": False, "error": "ssh_connection_test_failed"}), 400


if __name__ == "__main__":
    host = env_get("HOST", "127.0.0.1")
    port = int(env_get("PORT", "5000"))
    debug = env_get("FLASK_DEBUG", "0") == "1"
    app.run(host=host, port=port, debug=debug, use_reloader=False)

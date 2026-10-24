from flask import Flask, request, jsonify, render_template
from werkzeug.exceptions import HTTPException
from kristall import KristallError
import os
import logging
import json
from core.config import TEXT_MODELS, VOICE_MODELS, get_donation_url
from core.model_profiles import annotate_catalog
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
from identity.user_identity import init_user_identity_table, register_anonymous_user
from organization.departments import departments_bp, init_department_tables
from organization.government import (
    government_bp,
    init_government_tables,
    ensure_government_department,
)
from remote_runtime.environment_routes import environment_bp, environment_gateway_bp
from remote_runtime.environments import init_environment_tables
from identity.short_token_auth import install_short_token_auth
from identity.deployment_admin import deployment_admin_denied, install_deployment_gate
from rdc_connection.pairing import install_rdc_pairing
from invocation.problems import problem
from observability import system_log
from identity.github_oauth import (
    current_github_login,
    github_auth_bp,
    github_login_enabled,
    login_path,
)
from identity.user_identity import init_github_accounts_table
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
from forge import init_printing_tables
from forge.routes import printing3d_bp

app = Flask(__name__)
system_log.configure()
install_short_token_auth(app)
install_deployment_gate(app)
install_rdc_pairing(app)


def preview_base_path():
    """Return the request-scoped preview prefix, falling back to deployment config."""
    scoped = current_runtime_base_path()
    if scoped:
        return scoped
    return os.environ.get("LOGOS_PREVIEW_BASE_PATH", "").rstrip("/")


def _static_asset_version():
    """Return a stable version token that changes when the deployed assets change."""
    configured = os.environ.get("LOGOS_STATIC_VERSION")
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
app.register_blueprint(github_auth_bp)
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
    init_user_identity_table()
    init_github_accounts_table()
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
    if not _storage_ready and request.path != "/healthz":
        _initialize_storage()


@app.route("/")
def index():
    selected_conversation = None
    selected_messages = []
    conversation_id = (request.args.get("conversation_id") or "").strip()
    if conversation_id:
        owner_id = get_current_owner_id(required=False)
        if owner_id:
            selected_conversation = get_owned_conversation(conversation_id, owner_id)
        if selected_conversation:
            selected_messages = get_messages(conversation_id)

    return render_template(
        "index.html",
        preview_base_path=preview_base_path(),
        static_version=STATIC_ASSET_VERSION,
        selected_conversation=selected_conversation,
        selected_messages=selected_messages,
        donation_url=get_donation_url(),
        github_login_enabled=github_login_enabled(),
        github_login_url=login_path(),
        github_login=current_github_login(),
    )


@app.route("/healthz", methods=["GET"])
def healthz():
    return jsonify({"status": "ok"})


@app.route("/api/users/bootstrap", methods=["POST"])
def bootstrap_anonymous_user():
    raw_body = request.get_data(cache=True)
    payload = request.get_json(silent=True)
    if not raw_body:
        data = {}
    elif not isinstance(payload, dict):
        return jsonify({"error": "request body must be an object"}), 400
    else:
        data = payload

    if "metadata" not in data:
        metadata = {}
    else:
        metadata = data["metadata"]
        if not isinstance(metadata, dict):
            return jsonify({"error": "metadata must be an object"}), 400

    try:
        identity = register_anonymous_user(data.get("installation_id"), metadata)
        response = jsonify(identity)
        response.set_cookie(
            "logos_user_token",
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
    save_conv_settings(conv_id, request.get_json(silent=True) or {})
    return jsonify({"status": "ok"})


from agent_memory.manager import load_memory_config, save_memory_config, clear_global_memory
from agent_memory.extractor import list_global_memory_facts


def _memory_owner_denied():
    """Global memory is administered by an authenticated owner only."""
    try:
        get_current_owner_id()
    except TreasuryIdentityError as exc:
        return jsonify({"error": str(exc)}), 401
    return None


@app.route("/api/memory/manage", methods=["GET"])
def api_memory_panel_data():
    denied = _memory_owner_denied()
    if denied:
        return denied
    cfg = load_memory_config()
    facts = list_global_memory_facts()
    return jsonify({"config": cfg, "facts": facts})


@app.route("/api/memory/config", methods=["PUT"])
def api_update_memory_config():
    denied = _memory_owner_denied()
    if denied:
        return denied
    data = request.get_json(silent=True) or {}
    try:
        save_memory_config(data)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"status": "ok", "config": load_memory_config()})


@app.route("/api/memory/clear", methods=["POST"])
def api_clear_memory():
    denied = _memory_owner_denied()
    if denied:
        return denied
    category = (request.get_json(silent=True) or {}).get("category")
    if category is not None and not isinstance(category, str):
        return jsonify({"error": "category must be a string"}), 400
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
    # A credit creates balance that pays for provider calls, so only the deployment administrator
    # may mint it. Anyone can read their own balance.
    denied = deployment_admin_denied()
    if denied:
        return denied
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
    denied = deployment_admin_denied()
    if denied:
        return denied
    try:
        return jsonify(public_settings())
    except TreasuryIdentityError as exc:
        return jsonify({"error": str(exc)}), 401
    except Exception as exc:
        logger.exception("Failed to load SSH Runtime settings")
        return jsonify({"error": str(exc)}), 400


@app.route("/api/ssh-runtime/settings", methods=["PUT"])
def put_ssh_runtime_settings():
    denied = deployment_admin_denied()
    if denied:
        return denied
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
    denied = deployment_admin_denied()
    if denied:
        return denied
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
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5000"))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host=host, port=port, debug=debug, use_reloader=False)

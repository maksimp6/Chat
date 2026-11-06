"""What the web process serves, and in which order: tools, blueprints, the shell, routes.

app.py calls ``install(app)`` once. The order is the one app.py always used: the
ChatGPT bridge tools join the shared registry after its own sources, the RDC pairing
route comes first, blueprints are registered in the order below, and the shell
creates the durable tables (``STORAGE_INITIALIZERS``, in this order) at boot when a
memory path is configured, otherwise on the first data request.
"""

from __future__ import annotations

import time
from functools import partial

from webframe import Application

from agent_memory.routes import install as install_memory_routes
from agent_runtime.local_gateway import local_agent_bp
from chat.classification_routes import classification_bp
from chat.completion_routes import completion_bp
from chat.embedding_routes import embedding_bp
from chat.image_routes import image_bp
from chat.mcp_routes import mcp_bp
from invocation.manager import fail_interrupted_invocations
from chat.voice_routes import voice_bp
from cloud.cloudru.iam_routes import cloudru_iam_bp
from conversations.routes import install as install_conversation_routes
from court.hearings import court_bp
from rag_sync.routes import rag_bp
from vector_db.routes import vectors_bp
from economy.treasury_routes import install as install_treasury_routes
from forge import prepare_printing_records
from forge.routes import printing3d_bp
from mcp_server import chatgpt_mcp_bp
from mcp_server.storage import ensure_rdc_server
from mcp_server.tools import register_mcp_tools
from organization.departments import departments_bp
from organization.government import ensure_government_department, government_bp
from organization.partner_relations import (
    ensure_partner_department,
    prepare_partner_relations_records,
    partner_relations_bp,
)
from plugin_system.routes import plugin_bp
from provider_accounts.credentials_routes import provider_credentials_bp
from provider_accounts.model_routes import install as install_model_routes
from rdc_connection.pairing import install_rdc_pairing
from remote_runtime.environment_routes import environment_bp, environment_gateway_bp
from remote_runtime.environments import prepare_environment_records
from remote_runtime.ssh_routes import install as install_ssh_routes
from runtime.api import runtime_bp
from storage.records import open_journal_store
from web_shell import install as install_web_shell
from workspace.file_routes import file_bp
from workspace.upload_routes import upload_bp
from workspace.project_tree import project_tree_bp

BLUEPRINTS = (
    mcp_bp,
    chatgpt_mcp_bp,
    file_bp,
    upload_bp,
    runtime_bp,
    local_agent_bp,
    cloudru_iam_bp,
    provider_credentials_bp,
    partner_relations_bp,
    departments_bp,
    government_bp,
    environment_bp,
    environment_gateway_bp,
    plugin_bp,
    project_tree_bp,
    voice_bp,
    image_bp,
    embedding_bp,
    classification_bp,
    completion_bp,
    printing3d_bp,
    court_bp,
    rag_bp,
    vectors_bp,
)

# Turns opened before this process booted were killed with the previous one (#1710).
_BOOTED_AT = time.time()

STORAGE_INITIALIZERS = (
    open_journal_store,
    prepare_printing_records,
    prepare_partner_relations_records,
    ensure_partner_department,
    ensure_government_department,
    prepare_environment_records,
    ensure_rdc_server,
    partial(fail_interrupted_invocations, before=_BOOTED_AT),
)


def install(app: Application) -> None:
    """Add every unit the web process serves to ``app``."""
    register_mcp_tools()
    install_rdc_pairing(app)
    for blueprint in BLUEPRINTS:
        app.register_blueprint(blueprint)
    install_web_shell(app)
    install_model_routes(app)
    install_conversation_routes(app)
    install_memory_routes(app)
    install_treasury_routes(app)
    install_ssh_routes(app)

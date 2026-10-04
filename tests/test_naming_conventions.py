"""Guardrails for docs/development/naming.md.

Each allowlist holds known violations. New violations fail, and a fixed entry
must be removed from its allowlist, so the lists only shrink.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates" / "index.html"

# #430: implementation belongs in packages. Existing root modules wait for
# their subsystem slice; do not add new ones here without a justification.
ROOT_MODULE_ALLOWLIST = {
    "agent_gateway",
    "alice_agent_runner",
    "api_contracts",
    "app",
    "archiver",
    "billing",
    "budget_controller",
    "budget_repository",
    "chatgpt_mcp",
    "cli_agent",
    "cloudru_api_key_provider",
    "cloudru_iam",
    "cloudru_iam_routes",
    "compute_resources",
    "config",
    "conversation_metadata",
    "conversation_ownership",
    "credential_crypto",
    "db",
    "db_backend",
    "departments",
    "environment_manager",
    "environment_routes",
    "file_manager",
    "file_routes",
    "filesystem_mcp_tools",
    "git_mcp_tools",
    "government",
    "key_manager",
    "knowledge_economics",
    "local_agent_gateway",
    "local_tool_agent",
    "logger",
    "mcp_routes",
    "mcp_storage",
    "mcp_trace",
    "memory_db",
    "memory_extractor",
    "memory_manager",
    "model_discovery",
    "observability_migrations",
    "partial_output",
    "partner_relations",
    "plugin_execution",
    "plugin_manager",
    "plugin_routes",
    "pricing_registry",
    "profiler_tools",
    "project_tree",
    "provider_credentials",
    "provider_credentials_routes",
    "provider_key_rotation",
    "provider_quota_routes",
    "provider_quotas",
    "reasoning_plan",
    "responses_tool_loop",
    "runtime_api",
    "runtime_migrations",
    "runtime_tools",
    "sdk",
    "send_logs",
    "session_manager",
    "session_profiles",
    "session_runtime",
    "short_token_auth",
    "ssh_runtime",
    "ssh_runtime_settings",
    "storage",
    "termux_mcp_tools",
    "termux_system_tools",
    "theme_tools",
    "tool_registry",
    "trace_manager",
    "trace_security",
    "trace_timing",
    "treasury",
    "treasury_identity",
    "universal_tool_platform",
    "user_identity",
    "voice_routes",
    "wikipedia_mcp_tools",
    "yandex_api_key_provider",
    "yandex_api_logger",
    "yandex_client",
    "yandex_metadata_validator",
    "yandex_request_builder",
    "yandex_request_utils",
    "yandex_response_parser",
    "yandex_response_poller",
    "yc_logging",
}

STATIC_FILE_ALLOWLIST = {"trace_viewer_auto.js", "trace_viewer_timing_fix.js"}

TEMPLATE_ID_ALLOWLIST = {
    "header-actions-2",
    "memoryModal",
    "memoryCloseBtn",
    "memoryModalTitle",
    "memEnabled",
    "memLimit",
    "memoryClearBtn",
    "memCount",
    "memoryFactsList",
}

UI_TEXT_ALLOWLIST: set[str] = set()

SNAKE_JS = re.compile(r"^[a-z][a-z0-9_]*\.js$")
PATCH_SUFFIX = re.compile(r"_(fix|new|old|tmp|auto|v\d+)(?=[_.])")
KEBAB_ID = re.compile(r"^[a-z][a-z0-9]*(-[a-z][a-z0-9]*)*$")
CYRILLIC = re.compile(r"[А-Яа-яЁё]")


def _static_js_names():
    return sorted(path.name for path in (ROOT / "static").rglob("*.js") if path.name != "eruda.js")


def _template_ids():
    return re.findall(r'\bid="([^"{}]+)"', TEMPLATE.read_text(encoding="utf-8"))


def _template_ui_texts():
    source = TEMPLATE.read_text(encoding="utf-8")
    texts = re.findall(r'\b(?:title|aria-label|placeholder)="([^"{}]+)"', source)
    return sorted(set(texts))


def _static_name_ok(name):
    return bool(SNAKE_JS.match(name)) and not PATCH_SUFFIX.search(name)


def test_no_new_root_python_modules():
    root_modules = {path.stem for path in ROOT.glob("*.py")}
    unexpected = sorted(root_modules - ROOT_MODULE_ALLOWLIST)
    assert not unexpected, f"new root modules belong in a package (#430): {unexpected}"
    moved = sorted(ROOT_MODULE_ALLOWLIST - root_modules)
    assert not moved, f"remove moved modules from ROOT_MODULE_ALLOWLIST: {moved}"


def test_static_js_file_names():
    names = _static_js_names()
    bad = [name for name in names if not _static_name_ok(name)]
    assert sorted(set(bad) - STATIC_FILE_ALLOWLIST) == []
    assert sorted(STATIC_FILE_ALLOWLIST - set(bad)) == [], "drop fixed names from the allowlist"


def test_template_ids_are_kebab_case():
    bad = [value for value in _template_ids() if not KEBAB_ID.match(value)]
    assert sorted(set(bad) - TEMPLATE_ID_ALLOWLIST) == []
    numbered = [value for value in _template_ids() if re.search(r"-\d+$", value)]
    assert sorted(set(numbered) - TEMPLATE_ID_ALLOWLIST) == []
    stale = TEMPLATE_ID_ALLOWLIST - set(bad) - set(numbered)
    assert not stale, f"drop fixed ids from the allowlist: {sorted(stale)}"


def test_template_ui_text_is_russian():
    bad = [text for text in _template_ui_texts() if not CYRILLIC.search(text)]
    assert sorted(set(bad) - UI_TEXT_ALLOWLIST) == []
    assert sorted(UI_TEXT_ALLOWLIST - set(bad)) == [], "drop fixed texts from the allowlist"


def test_rules_reject_known_bad_names():
    assert not _static_name_ok("TraceViewer.js")
    assert not _static_name_ok("chat_v2.js")
    assert not _static_name_ok("header_fix.js")
    assert _static_name_ok("execution_surface.js")
    assert not KEBAB_ID.match("memoryModal")
    assert KEBAB_ID.match("model-modal")

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PROJECT_ROOT_PYTHON = {
    "app.py",
    "config.py",
}

# Existing debt only. New root Python modules must not be added here.
# Delete entries as modules move into packages.
LEGACY_ROOT_PYTHON = {
    "agent_gateway.py",
    "alice_agent_runner.py",
    "api_contracts.py",
    "archiver.py",
    "billing.py",
    "budget_controller.py",
    "budget_repository.py",
    "chatgpt_mcp.py",
    "cli_agent.py",
    "cloudru_api_key_provider.py",
    "cloudru_iam.py",
    "cloudru_iam_routes.py",
    "compute_resources.py",
    "conversation_metadata.py",
    "conversation_ownership.py",
    "credential_crypto.py",
    "db.py",
    "db_backend.py",
    "departments.py",
    "environment_manager.py",
    "environment_routes.py",
    "file_manager.py",
    "file_routes.py",
    "filesystem_mcp_tools.py",
    "git_mcp_tools.py",
    "government.py",
    "key_manager.py",
    "knowledge_economics.py",
    "local_agent_gateway.py",
    "local_tool_agent.py",
    "logger.py",
    "mcp_routes.py",
    "mcp_storage.py",
    "mcp_trace.py",
    "memory_db.py",
    "memory_extractor.py",
    "memory_manager.py",
    "model_discovery.py",
    "observability_migrations.py",
    "partial_output.py",
    "partner_relations.py",
    "plugin_execution.py",
    "plugin_manager.py",
    "plugin_routes.py",
    "pricing_registry.py",
    "profiler_tools.py",
    "project_tree.py",
    "provider_credentials.py",
    "provider_credentials_routes.py",
    "provider_key_rotation.py",
    "provider_quota_routes.py",
    "provider_quotas.py",
    "reasoning_plan.py",
    "responses_tool_loop.py",
    "runtime_api.py",
    "runtime_migrations.py",
    "runtime_tools.py",
    "sdk.py",
    "send_logs.py",
    "session_manager.py",
    "session_profiles.py",
    "session_runtime.py",
    "short_token_auth.py",
    "ssh_runtime.py",
    "ssh_runtime_settings.py",
    "storage.py",
    "supabase_startup_check.py",
    "supabase_trace_mirror.py",
    "termux_mcp_tools.py",
    "termux_system_tools.py",
    "theme_tools.py",
    "tool_registry.py",
    "trace_manager.py",
    "trace_mirror_integration.py",
    "trace_security.py",
    "trace_timing.py",
    "treasury.py",
    "treasury_identity.py",
    "universal_tool_platform.py",
    "user_identity.py",
    "voice_routes.py",
    "wikipedia_mcp_tools.py",
    "yandex_api_key_provider.py",
    "yandex_api_logger.py",
    "yandex_client.py",
    "yandex_metadata_validator.py",
    "yandex_request_builder.py",
    "yandex_request_utils.py",
    "yandex_response_parser.py",
    "yandex_response_poller.py",
    "yc_logging.py",
}

APPROVED_ROOT_PYTHON = PROJECT_ROOT_PYTHON | LEGACY_ROOT_PYTHON


def test_no_new_python_modules_are_added_to_repository_root():
    actual = {path.name for path in ROOT.glob("*.py")}
    unexpected = actual - APPROVED_ROOT_PYTHON
    assert not unexpected, (
        "New root Python modules are not allowed; place them in a package: "
        + ", ".join(sorted(unexpected))
    )


def test_invocation_modules_live_in_package_not_repository_root():
    legacy_names = {
        "invocation_api.py",
        "invocation_context.py",
        "invocation_manager.py",
        "invocation_trace.py",
    }
    still_in_root = sorted(name for name in legacy_names if (ROOT / name).exists())
    assert not still_in_root, f"Invocation modules still in root: {still_in_root}"

    package = ROOT / "invocation"
    assert (package / "__init__.py").is_file()
    assert (package / "api.py").is_file()
    assert (package / "context.py").is_file()
    assert (package / "manager.py").is_file()
    assert (package / "trace.py").is_file()


def test_browser_modules_live_in_package_not_repository_root():
    legacy_names = {"browser_adapters.py", "browser_capabilities.py"}
    still_in_root = sorted(name for name in legacy_names if (ROOT / name).exists())
    assert not still_in_root, f"Browser modules still in root: {still_in_root}"

    package = ROOT / "browser"
    assert (package / "__init__.py").is_file()
    assert (package / "adapters.py").is_file()
    assert (package / "capabilities.py").is_file()


def test_pruned_legacy_agent_modules_stay_deleted():
    pruned = {
        "agent_context.py",
        "agent_runner.py",
        "agent_tools.py",
        "run_agent.py",
        "run_agent_loop.py",
        "yandex_agent_loop.py",
    }
    resurrected = sorted(name for name in pruned if (ROOT / name).exists())
    assert not resurrected, f"Pruned legacy agent loops were restored: {resurrected}"

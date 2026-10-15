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
    "cli_agent.py",
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
    "government.py",
    "key_manager.py",
    "knowledge_economics.py",
    "local_agent_gateway.py",
    "local_tool_agent.py",
    "logger.py",
    "mcp_routes.py",
    "memory_db.py",
    "memory_extractor.py",
    "memory_manager.py",
    "model_discovery.py",
    "partial_output.py",
    "partner_relations.py",
    "plugin_execution.py",
    "plugin_manager.py",
    "plugin_routes.py",
    "pricing_registry.py",
    "project_tree.py",
    "provider_credentials.py",
    "provider_credentials_routes.py",
    "provider_key_rotation.py",
    "provider_quota_routes.py",
    "provider_quotas.py",
    "reasoning_plan.py",
    "responses_tool_loop.py",
    "runtime_api.py",
    "runtime_tools.py",
    "sdk.py",
    "session_manager.py",
    "session_profiles.py",
    "session_runtime.py",
    "ssh_runtime.py",
    "ssh_runtime_settings.py",
    "storage.py",
    "tool_registry.py",
    "trace_manager.py",
    "trace_security.py",
    "trace_timing.py",
    "treasury.py",
    "treasury_identity.py",
    "universal_tool_platform.py",
    "voice_routes.py",
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


def test_repository_agent_rules_have_single_canonical_source():
    assert (ROOT / "AGENTS.md").exists()
    assert not (ROOT / "CLAUDE.md").exists()

    codeowners = (ROOT / ".github" / "CODEOWNERS").read_text(encoding="utf-8")
    assert not any(line.split()[:1] == ["CLAUDE.md"] for line in codeowners.splitlines())


def test_tool_providers_live_in_package_not_repository_root():
    moved = {
        "termux_mcp_tools.py": "termux.py",
        "termux_system_tools.py": "termux_system.py",
        "filesystem_mcp_tools.py": "filesystem.py",
        "git_mcp_tools.py": "git.py",
        "wikipedia_mcp_tools.py": "wikipedia.py",
        "profiler_tools.py": "profiler.py",
        "theme_tools.py": "theme.py",
    }
    still_in_root = sorted(name for name in moved if (ROOT / name).exists())
    assert not still_in_root, f"Tool provider modules still in root: {still_in_root}"

    package = ROOT / "tool_providers"
    assert (package / "__init__.py").is_file()
    for module in moved.values():
        assert (package / module).is_file(), module


def test_filesystem_tools_stay_rooted_at_repository_root():
    from tool_providers import filesystem

    assert filesystem.BASE_DIR == str(ROOT.resolve())


MOVED_HELPERS = {
    "analyze_logs.sh",
    "analyze_modules.sh",
    "crl.sh",
    "debug.sh",
    "file.sh",
    "install_protobuf_env.sh",
    "test_api.sh",
    "view_logs.sh",
    "view_module_logs.sh",
}


def test_operational_helpers_live_in_scripts_without_root_shims():
    for name in sorted(MOVED_HELPERS):
        assert not (ROOT / name).exists(), f"Operational helper still in root: {name}"
        assert (ROOT / "scripts" / name).is_file(), name


def test_only_startup_shell_entrypoint_remains_in_root():
    assert {path.name for path in ROOT.glob("*.sh")} == {"start.sh"}


def test_root_inventory_covers_each_remaining_python_file():
    import re

    inventory = (ROOT / "docs/architecture/root-file-inventory.md").read_text(encoding="utf-8")
    documented = re.findall(r"\[([^] /]+\.py)\]\(../../[^)]+\)", inventory)
    actual = {path.name for path in ROOT.glob("*.py")}
    assert len(documented) == len(set(documented)), "Duplicate inventory entries"
    assert set(documented) == actual, "Update root file inventory when relocating modules"

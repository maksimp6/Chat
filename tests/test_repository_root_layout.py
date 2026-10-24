from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PROJECT_ROOT_PYTHON = {
    "app.py",
}

# Root Python modules are not allowed; every module lives in a package.
LEGACY_ROOT_PYTHON: set[str] = set()

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


def test_observability_modules_live_in_package_not_repository_root():
    legacy_names = {
        "logger.py",
        "yc_logging.py",
        "trace_manager.py",
        "trace_security.py",
        "trace_timing.py",
    }
    still_in_root = sorted(name for name in legacy_names if (ROOT / name).exists())
    assert not still_in_root, f"Observability modules still in root: {still_in_root}"
    package = ROOT / "observability"
    for name in (
        "__init__.py",
        "trace_manager.py",
        "trace_security.py",
        "trace_timing.py",
        "system_log.py",
    ):
        assert (package / name).is_file()


def test_economy_and_provider_modules_live_in_packages_not_repository_root():
    moved = {
        "economy": [
            "billing",
            "budget_controller",
            "budget_repository",
            "compute_resources",
            "knowledge_economics",
            "pricing_registry",
            "treasury",
            "treasury_identity",
        ],
        "provider_accounts": [
            "credentials",
            "credentials_routes",
            "key_rotation",
            "quota_routes",
            "quotas",
        ],
    }
    for package, modules in moved.items():
        assert (ROOT / package / "__init__.py").is_file()
        for module in modules:
            assert (ROOT / package / f"{module}.py").is_file(), f"{package}/{module}.py"
    assert not (ROOT / "billing.py").exists()
    assert not (ROOT / "provider_credentials.py").exists()


def test_agent_session_and_remote_runtime_modules_live_in_packages():
    moved = {
        "agent_runtime": [
            "gateway",
            "local_gateway",
            "local_tool_agent",
            "cli",
            "responses_tool_loop",
            "reasoning_plan",
        ],
        "sessions": ["manager", "profiles", "runtime"],
        "remote_runtime": ["ssh", "ssh_settings", "environments", "environment_routes"],
    }
    for package, modules in moved.items():
        assert (ROOT / package / "__init__.py").is_file()
        for module in modules:
            assert (ROOT / package / f"{module}.py").is_file(), f"{package}/{module}.py"
    assert not (ROOT / "session_manager.py").exists()
    assert not (ROOT / "agent_gateway.py").exists()


def test_workspace_plugin_organization_and_conversation_modules_live_in_packages():
    moved = {
        "workspace": ["files", "file_routes", "project_tree"],
        "plugin_system": ["manager", "routes", "execution"],
        "organization": ["government", "departments", "partner_relations"],
        "conversations": ["metadata", "ownership"],
    }
    for package, modules in moved.items():
        assert (ROOT / package / "__init__.py").is_file()
        for module in modules:
            assert (ROOT / package / f"{module}.py").is_file(), f"{package}/{module}.py"
    assert not (ROOT / "file_manager.py").exists()
    assert not (ROOT / "plugin_manager.py").exists()

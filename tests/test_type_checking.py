"""Type-checking ratchet: mypy --strict per docs/development/reliability.md.

Error counts per mypy code may only go down, and fully typed files stay fully
typed. A fix must lower the baseline (or add the newly clean file) in the
same change so the improvement is locked in.
"""

import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

MYPY_ERROR_BASELINE = {
    "arg-type": 46,
    "assignment": 48,
    "attr-defined": 48,
    "call-arg": 1,
    "call-overload": 2,
    "comparison-overlap": 2,
    "dict-item": 2,
    "index": 7,
    "misc": 1,
    "no-any-return": 36,
    "no-redef": 1,
    "no-untyped-call": 299,
    "no-untyped-def": 324,
    "operator": 10,
    "return": 1,
    "return-value": 6,
    "type-arg": 139,
    "union-attr": 16,
    "untyped-decorator": 52,
    "var-annotated": 3,
}

# Files with zero strict errors. Errors here fail outright.
STRICT_CLEAN_FILES = {
    "agent_rules/__init__.py",
    "agent_rules/controller.py",
    "steel/guardian/admission.py",
    "agent_runtime/provider_receipt.py",
    "forge/__init__.py",
    "forge/binding.py",
    "printing3d/store.py",
    "printing3d/slicer.py",
    "chat/__init__.py",
    "storage/__init__.py",
    "api/__init__.py",
    "yandex_client_modules/__init__.py",
    "core/__init__.py",
    "conversations/__init__.py",
    "workspace/__init__.py",
    "plugin_system/__init__.py",
    "organization/__init__.py",
    "agent_runtime/__init__.py",
    "sessions/__init__.py",
    "remote_runtime/__init__.py",
    "economy/__init__.py",
    "provider_accounts/__init__.py",
    "economy/budget_repository.py",
    "storage/db.py",
    "mcp_server/storage.py",
    "provider_accounts/quotas.py",
    "economy/treasury.py",
    "agent_memory/repository.py",
    "invocation/manager.py",
    "sessions/manager.py",
    "alice_pro/android_shim.py",
    "agent_context/__init__.py",
    "agent_context/cache.py",
    "agent_context/models.py",
    "agent_runtime/gateway.py",
    "agent_memory/__init__.py",
    "agent_memory/client.py",
    "agent_shell/store.py",
    "kristall/__init__.py",
    "kristall/store.py",
    "agent_office/__init__.py",
    "agent_office/dispatch_model.py",
    "agent_office/task_state.py",
    "agent_office/simulation.py",
    "agent_retrieval/__init__.py",
    "agent_retrieval/models.py",
    "agent_shell/__init__.py",
    "agent_skills/__init__.py",
    "agent_skills/registry.py",
    "alice_pro/printers/android_bridge_contract.py",
    "alice_pro/printers/api.py",
    "alice_pro/printers/bambu_a1mini.py",
    "alice_pro/printers/cloud_api.py",
    "alice_pro/printers/gateway.py",
    "alice_pro/printers/lan.py",
    "alice_pro/printers/lan_address.py",
    "alice_pro/printers/lan_socket.py",
    "alice_pro/printers/models.py",
    "alice_pro/printers/persistent_session.py",
    "alice_pro/printers/service.py",
    "alice_mouse/__init__.py",
    "alice_mouse/core.py",
    "alice_mouse/security/__init__.py",
    "alice_mouse/security/grants.py",
    "alice_mouse/security/socket_boundary.py",
    "alice_board/__init__.py",
    "alice_board/seed.py",
    "alice_board/server.py",
    "alice_board/store.py",
    "alice_platform/__init__.py",
    "alice_platform/health.py",
    "alice_platform/planner.py",
    "alice_platform/providers/__init__.py",
    "alice_platform/providers/dns.py",
    "alice_platform/providers/storage.py",
    "alice_platform/reconciler.py",
    "alice_platform/recovery.py",
    "archiver.py",
    "keel/__init__.py",
    "keel/health.py",
    "keel/planner.py",
    "keel/providers/__init__.py",
    "keel/providers/dns.py",
    "keel/providers/storage.py",
    "keel/reconciler.py",
    "keel/recovery.py",
    "storage/archiver.py",
    "browser/__init__.py",
    "browser/emulator_adapter.py",
    "browser/capabilities.py",
    "browser/orchestration.py",
    "browser/pipeline.py",
    "browser/semantic.py",
    "chatgpt_mcp.py",
    "agent_runtime/cli.py",
    "cloud/__init__.py",
    "cloud/base.py",
    "cloud/cloudru/__init__.py",
    "cloud/cloudru/client.py",
    "cloud/models.py",
    "cloud/policy.py",
    "cloud/registry.py",
    "conversations/ownership.py",
    "identity/__init__.py",
    "invocation/__init__.py",
    "invocation/context.py",
    "invocation/problems.py",
    "economy/knowledge_economics.py",
    "agent_runtime/local_tool_agent.py",
    "mcp_server/runtime_bridge.py",
    "mcp_server/trace.py",
    "plugin_system/manager.py",
    "economy/pricing_registry.py",
    "printing3d/__init__.py",
    "printing3d/gcode_audit.py",
    "printing3d/model_preflight.py",
    "printing3d/preprint_readiness.py",
    "printing3d/quality_capability.py",
    "printing3d/ftps_storage.py",
    "printing3d/print_workflow.py",
    "printing3d/video_archive.py",
    "provider_key_rotation.py",
    "printing3d/shape_benchmarks.py",
    "provider_accounts/key_rotation.py",
    "repository_index/__init__.py",
    "repository_index/provenance.py",
    "rdc_connection/__init__.py",
    "agent_runtime/reasoning_plan.py",
    "agent_runtime/responses_tool_loop.py",
    "agent_runtime/logos_launch_hold.py",
    "agent_runtime/generation_gate.py",
    "runtime/__init__.py",
    "runtime/conversation_agents.py",
    "runtime/loader.py",
    "runtime/request_context.py",
    "secret_store/__init__.py",
    "secret_store/cloudru.py",
    "secret_store/core.py",
    "secret_store/fake.py",
    "secret_store/manager.py",
    "secret_store/admin.py",
    "secret_store/fake_admin.py",
    "secret_store/files.py",
    "secret_store/file_alias_store.py",
    "secret_store/delivery.py",
    "agent_memory/conversation_ownership_store.py",
    "agent_memory/user_identity_store.py",
    "sessions/profiles.py",
    "remote_runtime/ssh.py",
    "storage/contracts.py",
    "observability/__init__.py",
    "observability/system_log.py",
    "observability/trace_timing.py",
    "tool_providers/__init__.py",
    "tools/android/rdc_actions.py",
    "user_identity.py",
    "treasury_identity.py",
    "yandex_api_logger.py",
    "identity/user_identity.py",
    "economy/treasury_identity.py",
    "yandex_client_modules/api_logger.py",
    "yandex_client_modules/request_receipt.py",
}

ERROR_LINE = re.compile(r"^(?P<path>[^:]+\.py):\d+(?::\d+)?: error: .*\[(?P<code>[a-z-]+)\]$")
EXCLUDED = re.compile(r"^(tests|node_modules|android|scripts|deploy|plugins|r)/")


def _mypy_errors():
    result = subprocess.run(
        [sys.executable, "-m", "mypy", "."],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=600,
    )
    if result.returncode not in (0, 1):
        raise AssertionError(f"mypy crashed:\n{result.stdout}\n{result.stderr}")
    errors = []
    for line in result.stdout.splitlines():
        match = ERROR_LINE.match(line)
        if match:
            errors.append((match["path"], match["code"]))
    return errors


def _python_files():
    listed = subprocess.run(
        ["git", "ls-files", "*.py"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    return {path for path in listed if not EXCLUDED.match(path)}


def test_strict_type_errors_only_go_down():
    errors = _mypy_errors()
    counts = Counter(code for _path, code in errors)
    codes = set(counts) | set(MYPY_ERROR_BASELINE)
    grown = {
        code: (counts[code], MYPY_ERROR_BASELINE.get(code, 0))
        for code in codes
        if counts[code] > MYPY_ERROR_BASELINE.get(code, 0)
    }
    assert not grown, f"new mypy --strict errors (code: (now, allowed)): {grown}"
    shrunk = {
        code: (counts[code], limit)
        for code, limit in MYPY_ERROR_BASELINE.items()
        if counts[code] < limit
    }
    assert not shrunk, f"lower MYPY_ERROR_BASELINE to lock in the fixes: {shrunk}"

    dirty = {path for path, _code in errors}
    regressed = sorted(STRICT_CLEAN_FILES & dirty)
    assert not regressed, f"fully typed files gained mypy errors: {regressed}"
    newly_clean = sorted(_python_files() - dirty - STRICT_CLEAN_FILES)
    assert not newly_clean, f"add newly typed files to STRICT_CLEAN_FILES: {newly_clean}"


def test_mypy_python_version_matches_ci_runtime():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    runtime = (ROOT / ".python-version").read_text(encoding="utf-8").strip()
    assert 'python_version = "3.14"' in pyproject
    assert runtime == "3.14"

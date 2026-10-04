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
    "arg-type": 55,
    "assignment": 52,
    "attr-defined": 48,
    "call-arg": 2,
    "call-overload": 2,
    "comparison-overlap": 2,
    "dict-item": 2,
    "index": 7,
    "misc": 1,
    "no-any-return": 48,
    "no-redef": 3,
    "no-untyped-call": 481,
    "no-untyped-def": 451,
    "operator": 27,
    "return": 1,
    "return-value": 11,
    "type-arg": 164,
    "union-attr": 16,
    "untyped-decorator": 52,
    "var-annotated": 5,
}

# Files with zero strict errors. Errors here fail outright.
STRICT_CLEAN_FILES = {
    "agent_context/__init__.py",
    "agent_context/cache.py",
    "agent_context/models.py",
    "agent_gateway.py",
    "agent_memory/__init__.py",
    "agent_office/__init__.py",
    "agent_office/dispatch_model.py",
    "agent_office/task_state.py",
    "agent_retrieval/__init__.py",
    "agent_retrieval/models.py",
    "agent_shell/__init__.py",
    "agent_skills/__init__.py",
    "agent_skills/registry.py",
    "alice_platform/__init__.py",
    "alice_platform/health.py",
    "alice_platform/planner.py",
    "alice_platform/providers/__init__.py",
    "alice_platform/providers/dns.py",
    "alice_platform/providers/storage.py",
    "alice_platform/reconciler.py",
    "alice_platform/recovery.py",
    "archiver.py",
    "browser/__init__.py",
    "browser/capabilities.py",
    "browser/orchestration.py",
    "browser/pipeline.py",
    "browser/semantic.py",
    "chatgpt_mcp.py",
    "cli_agent.py",
    "cloud/__init__.py",
    "cloud/base.py",
    "cloud/cloudru/__init__.py",
    "cloud/cloudru/client.py",
    "cloud/models.py",
    "cloud/policy.py",
    "cloud/registry.py",
    "identity/__init__.py",
    "invocation/__init__.py",
    "invocation/context.py",
    "invocation/problems.py",
    "knowledge_economics.py",
    "local_tool_agent.py",
    "mcp_server/runtime_bridge.py",
    "mcp_trace.py",
    "plugin_manager.py",
    "pricing_registry.py",
    "printing3d/__init__.py",
    "provider_key_rotation.py",
    "rdc_connection/__init__.py",
    "reasoning_plan.py",
    "responses_tool_loop.py",
    "runtime/__init__.py",
    "runtime/conversation_agents.py",
    "runtime/loader.py",
    "runtime/request_context.py",
    "session_profiles.py",
    "ssh_runtime.py",
    "storage.py",
    "trace_timing.py",
    "treasury_identity.py",
    "yandex_api_logger.py",
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

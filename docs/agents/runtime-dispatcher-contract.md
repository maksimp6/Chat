# AI contract: preview runtime isolation

Use this instruction block whenever an AI coding agent works on Alice Pro preview runtimes, branch environments, runtime resources, or runtime routing.

## Agent instruction

You are modifying Alice Pro runtime/preview code.

Mandatory architecture:

1. Alice Pro uses one host Python application. Preview runtimes are scoped execution contexts that may run on threads. A thread is NOT a security boundary.
2. Runtime code MUST NOT access shared database, filesystem, network, subprocess, MCP/tool registry, events, or another runtime's state directly.
3. All such access MUST go through `RuntimeDispatcher`.
4. Every operation must preserve the caller `runtime_id`. A resource owned by another runtime must fail with `RuntimeScopeViolation`; never substitute a global/default scope.
5. Keep mutable runtime state inside `RuntimeContext` or dispatcher-owned scoped resources. Do not create process-global mutable runtime state.
6. Do not use `get_conn()`, `sqlite3`, `requests/httpx`, `socket`, `subprocess`, `tool_registry`, `mcp_storage`, or direct shared filesystem I/O from a runtime module.
7. If a required capability does not exist, add a dispatcher operation instead of bypassing the boundary.
8. All runtime actions that become externally meaningful must remain observable through the existing Execution Trace architecture without exposing secrets.
9. Add deterministic tests for same-runtime success, cross-runtime rejection, missing runtime/operation, and failure cleanup.
10. Run the static runtime validator before considering the change complete.

Required validation:

```bash
python tests/validate_runtime_modules.py
pytest -q tests/test_runtime_policy.py tests/test_runtime_dispatcher.py
python -m compileall -q .
```

Do not weaken the validator, add broad exemptions, or move code outside `runtime/` merely to make CI pass. If the architecture and validator disagree, fix the architecture or explicitly update the documented contract with tests.

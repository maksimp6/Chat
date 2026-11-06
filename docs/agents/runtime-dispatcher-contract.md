# AI contract: preview runtime isolation

Use this instruction block whenever an AI coding agent works on Logos preview runtimes, branch environments, runtime resources, or runtime routing.

## Agent instruction

You are modifying Logos runtime/preview code.

Mandatory architecture:

1. Logos uses one host Python application. Preview runtimes are scoped execution contexts that may run on threads. A thread is NOT a security boundary.
2. Runtime code MUST NOT access shared database, filesystem, network, subprocess, MCP/tool registry, events, or another runtime's state directly.
3. All such access MUST go through `RuntimeDispatcher`.
4. Every operation must preserve the caller `runtime_id`. A resource owned by another runtime must fail with `RuntimeScopeViolation`; never substitute a global/default scope.
5. Keep mutable runtime state inside `RuntimeContext` or dispatcher-owned scoped resources. Do not create process-global mutable runtime state.
6. Do not use `get_conn()`, `sqlite3`, `requests/httpx`, `socket`, `subprocess`, `tool_registry`, `mcp_storage`, or direct shared filesystem I/O from a runtime module.
7. If a required capability does not exist, add a dispatcher operation instead of bypassing the boundary.
8. All runtime actions that become externally meaningful must remain observable through the existing Execution Trace architecture without exposing secrets.
9. Add deterministic tests for same-runtime success, cross-runtime rejection, missing runtime/operation, and failure cleanup.

Do not add broad exemptions or move code outside `runtime/` to get around this contract.

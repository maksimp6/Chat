# Preview runtime dispatcher policy

Alice Pro preview runtimes share one Python application process. A runtime thread is an execution unit, not a security boundary. Isolation is therefore enforced by an explicit `RuntimeDispatcher` contract and by static/runtime tests.

## Mandatory boundary

A preview runtime may not directly access process-wide or externally shared resources.

Runtime modules under `runtime/` must use `RuntimeDispatcher.dispatch(...)` for operations that touch:

- database connections or repositories;
- filesystem reads/writes outside runtime-local in-memory state;
- HTTP/network clients and sockets;
- tool registry or MCP storage;
- subprocess/process control;
- cross-runtime events or shared mutable state;
- conversations, traces, files, tools, or records owned by a runtime scope.

The dispatcher is the only runtime boundary allowed to reach those capabilities.

## Scope rule

Every resource operation has a caller `runtime_id`. If a resource has a runtime owner, its `resource_runtime_id` must equal the caller runtime.

```text
runtime-a
   |
   v
RuntimeDispatcher
   |
   +-- resource_runtime_id == runtime-a -> execute
   |
   +-- resource_runtime_id == runtime-b -> RuntimeScopeViolation
```

Do not silently fall back to a global/default runtime when an id is missing.

## Threads are not isolation

Python threads share:

- process memory;
- `sys.modules`;
- module globals;
- environment variables;
- process credentials;
- file descriptors.

Therefore runtime code must not rely on thread separation alone. Mutable runtime state belongs in a `RuntimeContext` or in a resource obtained through the dispatcher.

## Static policy

`tests/validate_runtime_modules.py` parses runtime modules with Python AST before execution.

Outside the dispatcher boundary it rejects direct imports/calls for resource primitives such as:

- `db/get_conn`;
- `requests` / `httpx`;
- `socket`;
- `sqlite3`;
- `subprocess`;
- `tool_registry`;
- `mcp_storage`;
- direct `open()` and selected filesystem mutation/read helpers.

The list is intentionally conservative. When a new shared capability is added, extend the dispatcher first, then extend this policy and its regression tests.

## Required tests

Every new runtime capability must include:

1. a static-policy test proving direct access is rejected;
2. a dispatcher test proving the scoped operation is allowed;
3. a cross-runtime test proving runtime A cannot access runtime B;
4. failure-path coverage;
5. trace/redaction coverage when the operation emits Execution Trace data.

A green HTTP health check is not sufficient proof of runtime isolation.

## Commands

```bash
python tests/validate_runtime_modules.py
pytest -q tests/test_runtime_policy.py tests/test_runtime_dispatcher.py
```

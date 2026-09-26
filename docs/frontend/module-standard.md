# Alice Pro frontend module standard v1

Frontend modules use a C-like API boundary. A module receives a table of allowed Core API functions and does not access Core internals, another module's private state, or transport primitives directly.

## Module contract

Every module has:
- a stable `id`;
- a semantic `version`;
- an `apiVersion`;
- declared `dependencies`;
- declared `capabilities`;
- lifecycle entry points: `init`, `start`, `stop`, `destroy`.

The browser implementation uses `window.AliceCoreAPI`. Its shape is intentionally C-like: stable function tables, explicit boundaries, and capability checks.

```text
Module
  |
  | Module Context / Core API
  v
Core
  +-- status
  +-- trace
  +-- security
  +-- revocation
```

## User-visible system status

Modules publish current state through `Core.status`. The UI aggregates these states into the system-status indicator, similar to a vehicle dashboard.

Allowed states:

`ready`, `running`, `waiting`, `degraded`, `blocked`, `revoked`, `error`, `stopped`.

The module reports state and a short human-readable message. The UI decides presentation.

```js
context.status.set("running", "Чтение памяти");
```

The module does not manipulate the status panel directly.

## Tracing

Every module can create an operation trace through the Core API.

```js
const trace = context.trace.begin("load_facts", {
  source: "memory"
});

trace.event("started", {count: 10});
trace.toolCall("memory.read", {scope: "global"});
trace.response({count: 10});
trace.end("completed");
```

Trace payloads are sanitized at the Core boundary. Frontend traces are bounded in memory and are diagnostic context; server-side `ExecutionTrace` remains the authoritative persistent execution record.

## Sensitive-data boundary

Sensitive values must not reach traces, status metadata, events, or other diagnostic output.

Keys matching credential-like names are replaced with `[REDACTED]`, including API keys, authorization headers, passwords, tokens, credentials, cookies, and private keys.

Sanitization happens inside Core, even when a module forgets to sanitize.

```text
Module data
    |
    v
Security / Redaction
    |
    v
Trace / Status / Events
```

Modules must not depend on the caller to perform redaction.

## Revocation

Capabilities and module access can be revoked by Core.

```js
context.security.require("storage.read");

core.revocation.revoke(
  "capability",
  "memory:storage.read",
  "policy_changed"
);
```

A module-wide revocation invalidates its module access and all declared capabilities:

```js
core.revocation.revokeAll(
  "memory",
  "user_requested"
);
```

After revocation, capability checks fail with a controlled `Capability revoked` error. Revocation is not bypassed by retaining an older module context.

## Transport boundary

Frontend modules do not call `fetch` directly. All application transport goes through the Dispatcher.

```text
Module
  |
  v
Core API
  |
  v
Dispatcher
  |
  v
Network
```

The Dispatcher also records request-level frontend trace events and publishes live network status.

## Isolation rules

A module must not:
- access another module's private state;
- mutate another module's owned DOM;
- access another module's styles;
- call transport directly;
- use forbidden timer primitives;
- bypass capability checks;
- write unsanitized sensitive diagnostics.

Communication between modules happens through declared public APIs and Core events.

## Validation

CI validates frontend module code with:

```bash
python tests/validate_frontend_modules.py
pytest -q tests/test_frontend_module_code.py
node tests/test_system_status.js
pytest -q tests/test_unified_buttons_contract.py
```

The runtime contract test covers module registration, capabilities, status, traces, sensitive-data redaction, revocation, and the user-visible system-status panel.
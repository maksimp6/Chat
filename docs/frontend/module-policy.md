# Frontend module source policy

Alice Pro frontend JavaScript is treated as a set of modules with explicit runtime contracts. Source is validated before module execution.

## Mandatory checks

- JavaScript syntax must pass node --check.
- Named function declarations use lower camelCase names. Leading _, $, and PascalCase names are rejected.
- Dynamic code execution and common obfuscation primitives are rejected: eval, Function, atob/btoa, character-code construction, and long hex escape payloads.
- Binary/control-byte payloads and invalid UTF-8 are rejected.
- JavaScript files have a 512 KiB source limit.
- A source line may not exceed 8 KiB.
- Large embedded base64/data URLs are rejected.
- Repeated source text above the configured threshold is rejected.
- TODO/FIXME/XXX/HACK/NOTE markers are rejected from production frontend modules.
- Suspicious high-entropy or non-printable source is classified as unusual text and rejected.
- Vendor bundles are excluded from application policy checks and must be isolated explicitly. They are not treated as application modules.

## Loop and failure safety

Loops are treated as bounded resources, not as decorative syntax.

- Unbounded while (true) and for (;;) loops are rejected.
- while loops must have a statically visible termination condition.
- Network operations inside loops require an explicit bounded/cached design and are rejected by the static policy when no cache/memoization signal is visible.
- Runtime regression tests execute known infinite-loop cases under a hard VM timeout.
- Failure paths are tested through try/catch/finally to verify that loop cleanup is reached after an exception.
- Performance tests exercise bounded loops and enforce a runtime budget.
- New large array allocations are rejected above 4096 elements by the static policy.
- Runtime tests also verify that pathological array allocation cannot silently pass as an acceptable workload.

A timeout is a safety net, not a loop design. Production code must still have an explicit exit condition, cancellation path, or finite work budget.

## Caching policy

Repeated deterministic or repeatable lookups must not cause unnecessary network/storage work.

The validator looks for repeated fetch, localStorage.getItem, and sessionStorage.getItem operations and requires a visible cache/memoization mechanism (cache, memo, memoize, Map, or WeakMap) in the same source module. This is intentionally a static heuristic and is supplemented by runtime cache tests.

When a value can be reused safely, modules should cache it with:

1. a clear cache key;
2. an invalidation rule;
3. a bounded lifetime or explicit refresh path where freshness matters;
4. no unbounded cache growth.

## Timers and dispatcher

- Direct setTimeout, setInterval, clearTimeout, clearInterval, delay, and sleep calls are forbidden in frontend modules.
- Deferred work must use the module dispatcher and lifecycle/event mechanisms. This keeps cancellation, ownership, failure cleanup, and observability centralized.
- User/application requests must enter through the dispatcher. Modules do not call the transport directly.
- The dispatcher is the only frontend boundary allowed to reach the transport layer.

## Runtime contract

Static validation is only the first layer. The module runtime separately validates:

1. declared dependencies;
2. allowed tools/capabilities;
3. owned DOM;
4. public events;
5. public APIs;
6. lifecycle transitions;
7. style scope;
8. bounded execution and failure cleanup.

A module must not access another module's private state, DOM, styles, tools, or implementation details.

## Command

Run from the repository root:

    python tests/validate_frontend_modules.py

Runtime safety regression tests:

    node tests/test_frontend_runtime_safety.js

The CI pipeline runs both checks before the broader frontend regression suite.

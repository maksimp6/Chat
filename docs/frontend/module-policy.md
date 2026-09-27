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

## Source repetition classification

The repetition heuristic ignores blank lines and lines containing only structural
delimiters (`{}`, `()`, `[]`, semicolons, commas and whitespace). Such lines recur
naturally in formatted functions, objects and callbacks. Statements, operators,
comments and quoted literals are still counted. This is a source heuristic, not
a JavaScript parser or a proof of semantic duplication.

Both numerator and denominator of the duplicate ratio use substantive lines, so
padding a repeated payload with delimiter-only lines cannot dilute its ratio.
The existing limits are unchanged: 12 consecutive equal substantive lines, or a
duplicate ratio strictly greater than 0.35. Scattered occurrences of a common
statement are not a consecutive run. The ratio check still detects repeated
multiline blocks even when no individual run reaches 12.

### Issue #227 regression baseline

At `de8f97c8342c8c073c84885bf8f301bba5692b33`, `static/boot.js` had 16 standalone
closing-brace lines and a whole-file repeat ratio of approximately 0.22. The old
validator rejected it because it compared whole-file occurrence counts against
the consecutive-run limit. Formatting delimiters also inflated the ratio in
ordinary arrays and callbacks. This behavior was introduced by `303dbc95`, which
replaced the consecutive-run implementation in `90ce4d90`.

Regression tests first reproduce these false positives, then preserve rejection
of long runs, repeated blocks, obfuscated/base64 payloads and control bytes. Run:

    pytest -q tests/test_frontend_policy.py

No application JavaScript, transport boundaries or dependencies are changed by
this correction. Static checks do not establish UI behavior: BrowserShim/VM and
live Flask HTTP resource tests remain separate acceptance checks. Emulation does
not establish Android rendering, paint timing or real-network blank-screen behavior.

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

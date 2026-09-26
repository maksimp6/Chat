# Frontend module source policy

Alice Pro frontend JavaScript is treated as a set of modules with explicit runtime contracts. Source is validated before module execution.

## Mandatory checks

- JavaScript syntax must pass `node --check`.
- Named function declarations use lower camelCase names. Leading `_`, `$`, and PascalCase names are rejected.
- Dynamic code execution and common obfuscation primitives are rejected: `eval`, `Function`, `atob`/`btoa`, character-code construction, and long hex escape payloads.
- Binary/control-byte payloads and invalid UTF-8 are rejected.
- JavaScript files have a 512 KiB source limit.
- A source line may not exceed 8 KiB.
- Large embedded base64/data URLs are rejected.
- Repeated source text above the configured threshold is rejected.
- TODO/FIXME/XXX/HACK/NOTE markers are rejected from production frontend modules.
- Suspicious high-entropy or non-printable source is classified as unusual text and rejected.
- Vendor bundles are excluded from application policy checks and must be isolated explicitly. They are not treated as application modules.

The validator emits only errors. A passing validation therefore means `0 errors, 0 warnings, 0 notes`.

## Runtime contract

Static validation is only the first layer. The module runtime separately validates:

1. declared dependencies;
2. allowed tools/capabilities;
3. owned DOM;
4. public events;
5. public APIs;
6. lifecycle transitions;
7. style scope.

A module must not access another module's private state, DOM, styles, tools, or implementation details.

## Command

Run from the repository root:

    python tests/validate_frontend_modules.py

The CI pipeline runs this check before frontend regression tests.

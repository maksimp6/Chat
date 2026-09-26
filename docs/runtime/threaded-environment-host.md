# Threaded environment host

Alice Pro application environments run inside one Python process. Each running
environment owns a managed worker thread and an immutable `RuntimeContext`.

## Request path

```text
HTTP request
    -> environment gateway
    -> environment worker queue
    -> RuntimeDispatcher
    -> scoped operation
    -> streamed response queue
    -> HTTP response
```

The gateway does not proxy to a localhost port and the manager does not spawn a
Flask process for each environment. Runtime HTTP responses are streamed through
a bounded queue so slow clients apply backpressure instead of forcing the whole
response into memory.

## Scope

Each environment registers exactly one dispatcher scope containing:

- `runtime_id`;
- `owner_id`;
- data namespace;
- runtime-local data root.

The worker executes operations only after `RuntimeDispatcher` binds that
runtime id. Cross-runtime resource access remains subject to the dispatcher
policy in `runtime-dispatcher-policy.md`.

### Database scope

The runtime data root is carried with the worker through request-local
`ContextVar` state. `db.get_conn()` detects that scope and opens
`<runtime-data-root>/alice_pro.db` instead of the process database.

This rule also applies when the host is configured for PostgreSQL or the
in-memory backend: a preview runtime does not silently inherit either shared
backend. Its SQLite schema is initialized inside the runtime scope before the
worker starts. The environment control-plane database remains outside that
scope.

## Request-local URL state

Runtime URL prefixes are held in `ContextVar` state for the duration of the
worker request. The host no longer needs to mutate
`ALICE_PREVIEW_BASE_PATH` per environment. The environment value therefore
cannot leak from one worker thread into another through process-global
environment variables.

## Lifecycle

`EnvironmentManager` keeps the existing persistent environment records, but
`runtime_pid` and `runtime_port` are no longer runtime identities. They stay
null for threaded runtimes. The API exposes the live `runtime_thread_id` from
the in-process worker registry.

Stopping an environment cancels active response streams, joins its worker, and
unregisters the dispatcher scope. Deleting it additionally removes the immutable
git worktree and runtime-local data directory.

## Current boundary

The immutable worktree still records the selected branch/commit and provides a
runtime-local source root. This slice deliberately does not import arbitrary
branch Python modules into the shared interpreter. Python modules, globals,
credentials, and memory are process-wide, so loading mutually incompatible or
untrusted branch code by manipulating `sys.path` or `sys.modules` would only
pretend to provide isolation.

Until a branch-safe application loading contract is implemented, HTTP execution
uses the host Alice Pro application under the selected runtime context. This is
an architectural migration step, not a claim that threads are a sandbox.

The deployment migration that removes one-container-per-preview infrastructure
is a separate follow-up. Existing public preview deployment remains unchanged
until that follow-up has its own health, rollback, and routing coverage.

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

## Branch-safe loader contract

The manager resolves the requested ref before creating an environment, creates
a detached worktree for that exact commit, and refuses to start from a dirty or
mismatched snapshot. If the snapshot contains `alice_runtime.py`, `RuntimeLoader`
loads that single entry point into a private namespace and passes it a stable
host capability object. It never changes `sys.path`, environment variables, or
`sys.modules`. Each environment receives a distinct namespace and application
instance, including when two revisions run simultaneously.

The entry point must define `create_runtime(host)` and return an object with
`invoke(operation, payload)`. Imports (including dynamic import) are rejected in
this first slice. Host resources are reached only through the supplied API and
`RuntimeDispatcher`; invocation uses the `revision.invoke` dispatcher operation.

This contract prevents accidental module and module-global collisions; it does
not make arbitrary or hostile Python safe. Python reflection, native extensions,
monkey-patching, CPU/memory exhaustion, and process memory cannot be securely
isolated between threads. Code that needs unrestricted imports or is not trusted
must not be loaded into this process. HTTP execution continues to use the stable
host application until a later slice defines a similarly constrained revision
HTTP contract, so existing streaming remains unchanged.

## Preview CI follow-up (#341)

Preview CI should build/test the requested commit, then authenticate to the
running Alice Pro host and request creation of an environment for that exact
commit. It should poll the environment lifecycle and exercise the rendered shell
through the environment gateway. Cleanup should delete that environment. It
must not deploy a preview container, wait for a container-local Flask server, or
discover/use a `runtime_port`. The host response and lifecycle trace should be
the CI evidence tying the tested commit to the runtime.

The deployment migration that removes one-container-per-preview infrastructure
is a separate follow-up. Existing public preview deployment remains unchanged
until that follow-up has its own health, rollback, and routing coverage.

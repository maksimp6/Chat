# Branch Environments: Architecture Review

**Status:** Pre-realization design review  
**Issue:** #13  
**PR:** #330  
**Decision:** Keep the feature as an Environment Manager, but do not treat the current PR implementation as the final production architecture.

## 1. Problem

Alice Pro needs multiple simultaneously usable application environments built from
different Git branches/commits.

An environment is an infrastructure boundary. It is not a Conversation,
Session, InvocationContext, or user-facing department.

The primary invariant is:

`Environment = immutable source revision + isolated runtime + isolated mutable state + deterministic routing`

A new commit must not silently mutate an existing environment.

## 2. Existing architecture to reuse

The implementation must build on existing project primitives rather than creating
parallel systems:

- `Session` and `InvocationContext` remain request/user context.
- `ExecutionTrace` remains the observability mechanism.
- Existing Preview Deployment infrastructure remains responsible for CI/PR
  previews where applicable.
- Existing authentication/owner identity remains the authorization boundary.
- Existing DB migration/backend conventions must be reused.
- Existing frontend API client/configuration must remain environment-relative.

Environment Manager is therefore a control-plane component, not a replacement for
the application's runtime/session architecture.

## 3. Target architecture

```
Git branch / PR
      |
      v
+----------------------+
| Environment Manager  |
| control plane        |
+----------+-----------+
           |
           +--> immutable commit SHA
           |
           +--> runtime artifact/worktree
           |
           +--> isolated state namespace
           |
           +--> route registration
           |
           +--> ExecutionTrace
           |
           v
+----------------------+
| Environment Runtime  |
| env_id + commit      |
+----------+-----------+
           |
           +--> frontend
           +--> API
           +--> Session
           +--> InvocationContext
           +--> tools / MCP
```

The control plane stores environment metadata in the system database. The runtime
must never choose its own branch or commit.

## 4. Environment identity

Each environment has:

- `environment_id`: immutable UUID;
- `branch_name`: informational Git ref;
- `commit_sha`: immutable source identity;
- `status`;
- `url`;
- `owner_id`;
- `data_namespace`;
- creation/update timestamps;
- runtime/deployment metadata.

The commit is resolved when the environment is created.

If both branch and explicit commit are supplied, the commit must be reachable from
that branch. This prevents a misleading branch label from being paired with
unrelated source code.

## 5. Runtime isolation

### Required

Every running environment must have:

1. a separate runtime/process/container;
2. immutable source at the recorded commit;
3. unique environment identity;
4. unique mutable-state namespace;
5. environment-specific routing;
6. no secret leakage into UI, Git, or traces.

### Runtime implementation stages

The first vertical slice may use a local process + detached Git worktree.

Production deployment should use a managed runtime adapter:

`EnvironmentManager -> RuntimeAdapter -> Docker/Podman/systemd/cloud runtime`

The Environment Manager must not permanently depend on one process model.

## 6. Data isolation

The target production model is logical isolation, not a separate database server
per environment.

For PostgreSQL:

`schema_env_<environment_id>`

For Redis/KV:

`env:<environment_id>:...`

For object/file storage:

`environment/<environment_id>/...`

The system database containing the Environment Manager remains outside those
namespaces.

### Current PR limitation

PR #330 uses a separate SQLite file for each local runtime. This is useful for
proving isolation and local development, but it is **not equivalent to production
PostgreSQL schema isolation**.

Therefore the acceptance criterion "runtime does not share mutable state" can be
tested locally, but the production DB namespace adapter remains a follow-up
implementation.

## 7. Routing and frontend/API pairing

The target external routing model is preferably:

`env-<stable-environment-token>.alicepro.dev`

The token must be derived from environment identity, not directly from an
untrusted branch name.

An environment runtime must derive API URLs from its own origin:

`window.location.origin + /api/...`

or receive an equivalent server-generated environment configuration.

This guarantees:

`frontend(environment X) -> API(environment X)`

and prevents accidental calls to master or another branch environment.

### Current PR limitation

PR #330 uses:

`/environments/<environment_id>/`

with an internal gateway.

This is valid as an initial vertical slice and is useful for local deployment,
but subdomain/wildcard routing remains the production routing adapter.

## 8. Lifecycle state machine

The canonical lifecycle is:

```
CREATING
   |
   v
STOPPED <----> RUNNING
   |
   v
DELETING

RUNNING -> FAILED
FAILED  -> STOPPED
```

Rules:

- source revision never changes in-place;
- restarting does not pull a new commit;
- a new commit means a new immutable environment;
- deletion of a Git branch does not automatically delete environment data;
- retention/deletion policy is explicit.

## 9. API contract

```
GET    /api/environments
POST   /api/environments
GET    /api/environments/<id>
POST   /api/environments/<id>/start
POST   /api/environments/<id>/stop
POST   /api/environments/<id>/restart
DELETE /api/environments/<id>
```

The detail response must expose at least:

- environment_id;
- branch;
- commit;
- status;
- URL;
- created/updated timestamps;
- owner-scoped visibility;
- runtime status/error without secrets.

## 10. Observability

Every lifecycle operation must produce an ExecutionTrace containing:

- `trace_id`;
- `environment_id`;
- operation;
- status;
- timing;
- branch;
- commit SHA;
- error metadata when applicable.

Trace data must not contain:

- API keys;
- access tokens;
- passwords;
- cookies;
- private credentials;
- unnecessary user PII.

Environment lifecycle traces should remain correlated with an InvocationContext
when the operation originates from a user request. Infrastructure-triggered
operations may have infrastructure-scoped correlation IDs.

## 11. Security boundaries

Environment APIs are control-plane APIs and must be owner/admin protected.

Never accept arbitrary shell commands from the API.

Git refs are passed as process arguments, never interpolated into shell commands.

Runtime configuration is server-controlled.

Environment IDs used for filesystem paths must be generated identifiers, not raw
branch names.

Public URLs must not expose secrets or internal runtime ports.

## 12. UI

The Environments panel should display:

| Field | Purpose |
|---|---|
| Branch | source context |
| Commit | immutable revision |
| Status | lifecycle state |
| URL | environment entry point |
| Created/updated | lifecycle history |
| Actions | start/stop/restart/delete |

The application itself should also expose a small environment badge so that a user
can immediately see which branch/commit is open.

## 13. CI and PR integration

The Environment Manager and CI Preview Deployment are related but separate:

- CI validates source;
- Preview Deployment publishes a CI/PR preview;
- Environment Manager stores and controls persistent environments.

A future integration may automatically create/update an environment after a
successful CI build, but it must preserve immutable commit identity.

## 14. Review of PR #330

### Accepted as first vertical slice

- Environment Manager control plane exists.
- Branch/ref resolves to immutable commit.
- Detached worktree is used.
- Runtime processes are isolated.
- Local mutable state is isolated by environment.
- Lifecycle API exists.
- Environment URL is paired with the environment gateway.
- ExecutionTrace events are persisted.
- UI exists.
- Parallel-environment tests exist.

### Not yet production-complete

1. PostgreSQL logical namespace adapter.
2. Production runtime adapter/container orchestration.
3. Production subdomain/wildcard routing.
4. Automatic frontend environment configuration/badge.
5. Deployment artifact/build strategy.
6. Environment retention policy and garbage collection.
7. Full lifecycle health/readiness checks.
8. CI status integration and automatic environment creation policy.

## 15. Implementation order

### Phase A: contract

- finalize Environment model;
- finalize state machine;
- finalize API;
- finalize trace contract;
- finalize security/ownership rules.

### Phase B: local vertical slice

- detached worktree;
- isolated process;
- isolated local DB;
- environment gateway;
- UI;
- integration tests.

### Phase C: production adapters

- PostgreSQL namespace adapter;
- Redis/storage prefixes;
- container/runtime adapter;
- reverse proxy/subdomain adapter;
- health checks;
- deployment artifact lifecycle.

### Phase D: CI integration

- PR/branch event;
- build;
- immutable commit environment;
- deployment;
- status synchronization;
- retention policy.

## 16. Decision

Do not close #13 merely because the local vertical slice satisfies the shape of
the REST API.

The issue should be considered complete only when the documented production
architecture is either implemented or the remaining adapter work is explicitly
split into follow-up issues.

The current PR should therefore be reviewed as **Phase B: first vertical slice**,
not as the final Environment Manager architecture.

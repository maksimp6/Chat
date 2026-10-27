# Branch-aware environments

Logos treats a branch environment as an immutable application runtime created
from one exact Git commit. It is infrastructure state, not a Session,
Conversation, or InvocationContext.

## Control plane

The Environment Manager stores:

- environment id;
- branch name;
- immutable commit SHA;
- lifecycle status;
- environment URL;
- runtime PID/port;
- unique data namespace;
- owner;
- timestamps and errors.

REST:

- GET /api/environments
- POST /api/environments with { "branch": "feature/x", "commit_sha": "optional" }
- GET /api/environments/<id>
- POST /api/environments/<id>/start
- POST /api/environments/<id>/stop
- POST /api/environments/<id>/restart
- DELETE /api/environments/<id>

A branch is resolved to a 40-character commit SHA at creation. Runtime code is
then checked out into a detached Git worktree at that SHA. Existing environments
are never updated when a branch advances.

## Isolation

Each environment receives its own worktree, process, runtime data directory,
native Kristall journal, environment id, namespace, branch and commit metadata.

`master` is the owner's personal branch. The current phone delivery route uses
the [develop runner](setup/redmi9-auto-update.md); this page does not establish a
public preview deployment. Access follows [shared local access](security/github-login.md).
The Environment Manager remains a separate application control-plane concept.

## Lifecycle and traces

Valid lifecycle transitions are:

CREATING -> STOPPED -> RUNNING -> STOPPED -> DELETING

A failed start transitions to FAILED. There is no automatic deletion when a Git
branch disappears. Deletion is explicit.

Every lifecycle operation creates an ExecutionTrace containing environment id,
branch, commit, operation and status. The sanitized trace is persisted in
environment_events.

## Runtime configuration

- LOGOS_ENV_REPO_ROOT: repository root, default current working directory.
- LOGOS_ENV_RUNTIME_ROOT: isolated runtime storage, default .logos-environments.
- LOGOS_ENV_RUNTIME_COMMAND: command inside the immutable worktree, default
  python app.py.
- LOGOS_ENV_PUBLIC_BASE_URL: public origin used to construct environment URLs.

Runtime configuration is server-side. Git input is passed as argument arrays and
never through a shell command.

## Relationship to #4

Session and InvocationContext are request/user-level concepts. Branch
Environment is the immutable infrastructure box containing those contexts.

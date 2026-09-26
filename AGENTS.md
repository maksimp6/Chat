# Alice Pro GitHub Agent instructions

## Repository workflow

Use the smallest independent change that satisfies an issue or subtask.

1. Inspect the issue and current `master`.
2. Create a branch named for the issue/change.
3. Implement the smallest vertical slice.
4. Add or update deterministic regression tests.
5. Run the relevant backend/Android checks in CI.
6. Publish the branch and open the PR yourself; do not stop at a local commit or "PR metadata".
7. Push the focused branch to `origin` and create the PR against current `master` using `gh pr create` or the available GitHub publication tool.
8. If publication is blocked by missing remote, credentials, network access, or tooling, report the exact failing command/error and do not claim that a PR exists.
9. Merge only after required checks and review policy are satisfied.
10. Never rewrite `master` directly and never commit secrets.

## Architecture rules

- Treat `UniversalToolExecutor` as the execution boundary for tool calls.
- Preserve `InvocationContext` and `ExecutionTrace` correlation across requests, tool calls, polling, and continuations.
- Keep secrets out of logs, traces, issues, tests, and client-visible errors.
- Prefer repository-local web UI assets. Do not introduce CDN-hosted UI dependencies without an explicit issue.
- Android changes must preserve system-bar safety and stable debug/release signing boundaries.
- PostgreSQL is an optional runtime backend selected only by ALICE_DATABASE_URL; SQLite remains the default local/Termux backend.
- Database schema changes must keep both SQLite and PostgreSQL paths working.
- Preview/runtime modules under `runtime/` must use `RuntimeDispatcher` for shared database, filesystem, network, process, MCP/tool-registry, event, and cross-runtime resource access.
- A preview runtime must never access another runtime's resources directly. Preserve the caller `runtime_id` and fail cross-runtime access with `RuntimeScopeViolation`.
- Python threads are execution units, not isolation boundaries. Do not use process-global mutable runtime state as a substitute for `RuntimeContext` or dispatcher-owned scoped resources.
- When delegating runtime/preview work to an AI agent, apply `docs/agents/runtime-dispatcher-contract.md` and keep `docs/runtime/runtime-dispatcher-policy.md` authoritative.

## Validation

Backend:
`python -m compileall -q .`
`python tests/validate_runtime_modules.py`
`pytest -q`

Android:
`gradle --no-daemon -PaliceBuildNumber=<run> -PaliceCommitHash=<sha> :app:testDebugUnitTest :app:assembleDebug`

For security-sensitive or schema changes, add focused regression coverage and document the operational setup required outside the repository.

# Alice Pro GitHub Agent instructions

## Repository workflow

Use the smallest independent change that satisfies an issue or subtask.

1. Inspect the issue and current `master`.
2. Create a branch named for the issue/change.
3. Implement the smallest vertical slice.
4. Add or update deterministic regression tests.
5. Run the relevant backend/Android checks in CI.
6. Open a PR describing the issue, behavior change, validation, and any migration/security impact.
7. Merge only after required checks and review policy are satisfied.
8. Never rewrite `master` directly and never commit secrets.

## Architecture rules

- Treat `UniversalToolExecutor` as the execution boundary for tool calls.
- Preserve `InvocationContext` and `ExecutionTrace` correlation across requests, tool calls, polling, and continuations.
- Keep secrets out of logs, traces, issues, tests, and client-visible errors.
- Prefer repository-local web UI assets. Do not introduce CDN-hosted UI dependencies without an explicit issue.
- Android changes must preserve system-bar safety and stable debug/release signing boundaries.
- Database changes go through committed Supabase migrations and the production migration workflow.

## Validation

Backend:
`python -m compileall -q .`
`pytest -q`

Android:
`gradle --no-daemon -PaliceBuildNumber=<run> -PaliceCommitHash=<sha> :app:testDebugUnitTest :app:assembleDebug`

For security-sensitive or schema changes, add focused regression coverage and document the operational setup required outside the repository.

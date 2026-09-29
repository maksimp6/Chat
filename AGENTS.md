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

## Agent dispatch

Describe the task with the "Agent task" issue template, then mention exactly one
agent in a comment:

- `@claude` — multi-file changes and investigations (`.github/workflows/claude.yml`).
- `@codex` — test engineer: checks each pull request's tests and proposes the
  missing regression tests; also takes focused, test-heavy issues. Uses
  `scripts/codex_setup.sh`.
- `@copilot` — assign the issue to Copilot; it also reviews pull requests
  (`.github/copilot-instructions.md`).
- `@alice` — self-improver: Alice Pro making small changes to her own code
  (UI, translations, prompts, her tools, docs) through her filesystem tools
  (`.github/workflows/alice.yml`, `docs/agents/alice-github-agent.md`).

Every agent opens one focused pull request per issue. Only the maintainer
merges, after green CI; agents never push to `master`.

## Maintainer

The maintainer is Claude, working from the Alice Pro project on claude.ai. The
owner (@maksimp6) does not merge by hand.

- Claude merges its own pull requests once CI is green on the current head and
  no review thread is open.
- Claude reviews pull requests from the other agents (`@codex`, `@copilot`,
  `@alice`) and merges them when CI is green and the review finds no blocking
  issue; otherwise it comments with what must change.
- Draft pull requests are not merged; the author marks them ready first.
  Alice always opens drafts, so the maintainer marks an Alice draft ready once
  its CI is green, then reviews it like any other pull request.
- Keep a pull request in working/draft state while implementation and CI are
  still changing. Do not request Codex or Copilot review during this phase.
- A pull request enters its single final review cycle only when the current head
  has passed required CI and there are no open review threads from earlier work.
  At that point mark it ready for review.
- Request GitHub Copilot review once for that ready-to-merge head. Fix or answer
  every Copilot comment and resolve each thread before merging.
- Request `@codex review` once in the same final review cycle. Add or answer
  every regression test Codex proposes. If Codex has not responded by the next
  hourly maintainer pass, Claude may continue once CI and other review gates are
  satisfied.
- Do not request another Codex or Copilot review after feedback fixes. The final
  corrected head is validated by required CI plus resolved review threads. A
  repeated review is allowed only when the owner explicitly asks for it.
- Merge is fail-closed. Immediately before merge, verify the pull request is
  synchronized with the current `master` (`behind master = 0`). Required
  checks must be green on the exact current PR head after that synchronization,
  not merely on an earlier head or an older base.
- If `master` advances before merge, synchronize the PR again and require the
  protected checks to pass on the new synced head. Do not reuse green checks
  from the stale head.
- After final-review fixes, do not request a second review cycle. Any new head
  still requires protected CI to pass and all review threads to remain resolved.
- Use protected auto-merge once the gate is satisfied. Never force-update,
  rewrite, or bypass protection on `master`.
- The owner's explicit approval is still required for production deployments,
  database migrations that change or drop existing data, and changes to
  secrets, CODEOWNERS or branch protection.

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

## Formatter policy

The only formatter entrypoints are:

`bash scripts/format.sh write`
`bash scripts/format.sh check`

All local, agent, and CI formatting must use those commands. Ruff is pinned by
`requirements-dev.txt`; Prettier is pinned by `package.json`. Workflows must
not install a floating/latest formatter version or maintain a second formatter
version in shell environment variables.

## Validation

Backend:
`python -m compileall -q .`
`python tests/validate_runtime_modules.py`
`pytest -q`

Android:
`gradle --no-daemon -PaliceBuildNumber=<run> -PaliceCommitHash=<sha> :app:testDebugUnitTest :app:assembleDebug`

For security-sensitive or schema changes, add focused regression coverage and document the operational setup required outside the repository.

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

Before dispatch or resuming an agent, apply the scoped work-admission checklist in
`docs/agents/role-based-agent-office.md`. Record the canonical task and current
stage/owner, expected output, role/backend and actual tool capability, required
approval, and stop conditions in the existing task handoff. Record the branch/head
for a write stage; use the relevant issue or PR head for read-only work. Contract
and contract-review begin from the issue and acceptance criteria; require accepted
contract evidence from implementation onward. Pause a stage when its applicable
prerequisites are missing or change; recheck before resuming.
This is a checkpoint on the existing TaskPacket and PR workflow, not a parallel
authority or status system.

## Agent dispatch

Alice Pro uses **roles first, model providers second**. The repository roles are
defined in `.github/agents/*.agent.md`; the underlying model/service is an
execution backend, not the employee identity.

### Role agents

For normal development, assign the issue through the GitHub Agents UI and choose
the narrowest matching repository role:

- **Team Lead** — triage, dependency analysis and delegation to specialists.
- **Backend Engineer** — Flask/API/runtime/tools/database/Execution Trace.
- **Frontend Engineer** — browser UI, static JavaScript, accessibility and
  BrowserShim-compatible behavior.
- **Android Engineer** — Android app, packaging, signing boundaries and device
  integration.
- **Test Engineer** — regression gaps, deterministic tests and CI diagnosis.
- **Infra Engineer** — GitHub Actions, runners, caching, Cloud.ru and deployment
  automation.
- **Security Reviewer** — read-mostly security review; no implementation.
- **Docs Engineer** — documentation and runbooks.
- **Release Manager** — merge/release readiness, versions, artifacts and rollback
  notes.
- **Operations Observer** — read-mostly detection of stalled coordination, retry loops,
  contradictory agent conclusions, and delegation cycles.
- **Process Governor** — evidence-backed process diagnosis and focused improvements to
  agent policy/skills/workflow, without taking over production implementation.

Team Lead may use the custom-agent tool to delegate normal implementation to the
engineering specialists. Operations Observer and Process Governor are supervisory
roles, not ordinary implementation owners. Keep one primary owner for an issue and
split only truly independent work.

### Observation and process governance

Use `docs/agents/process-observation-and-governance.md` when normal agent coordination
stops converging.

- Operations Observer watches repository evidence and emits a structured escalation
  only when a documented deterministic threshold is met. It does not choose the
  disputed implementation or edit files.
- Process Governor consumes repeated/structural escalations, classifies the process
  gap, and proposes the smallest process correction through a normal protected PR.
- An escalation is not a blame report. Prefer deterministic counts and state changes
  before spending model tokens on diagnosis.
- Process Governor must not edit application/runtime production code while acting in
  that role, expand its own authority, self-approve, or self-merge governance changes.
- Existing owner-approval boundaries remain authoritative for deployment, destructive
  data changes, secrets, CODEOWNERS, branch protection, permissions, and any expansion
  of agent authority.

### Execution backends and escalation

- `@claude` is reserved for the **Anthropic Claude GitHub Partner Agent**.
  Start it from the Agents UI or assign it to an issue; on an existing PR,
  mention the partner agent. Use **Claude Sonnet 4.6** for the normal strong
  path. Use it for architecture-heavy, ambiguous or multi-domain work.
- `@claude-lite` is our repository GitHub Actions worker
  (`.github/workflows/claude-lite.yml`). Its current execution profile is
  `claude-sonnet-4-6` with a maximum of 45 turns. Use this backend only when
  repository execution is intentional. In evidence/status prose that must not
  execute a workflow, write **Claude-Lite backend** without reproducing the
  literal mention trigger.
- `@codex` is the OpenAI GitHub Partner Agent. Routine Codex sessions should
  use **GPT-5.4 nano**; `.codex/config.toml` also sets nano/low defaults for
  repository Codex tooling.
- `@copilot` remains the native Copilot cloud agent and automatic PR reviewer.
  The role profiles above run on Copilot cloud agent with lightweight models
  unless explicitly escalated.
- `@alice` remains Alice Pro's self-improver for her own UI, translations,
  prompts, tools and docs.

Do not create a new vendor-named agent profile when a reusable engineering role
describes the work better. Prefer adding or refining a role profile, then choose
the cheapest backend/model that can reliably perform that role.

### Model policy

Routine role agents use `gpt-5.4-mini`; Docs Engineer uses
`gpt-5.4-nano`. Escalate to the Claude Partner Agent on Sonnet for a concrete
architecture/reasoning need. Do not silently escalate routine work to a larger
model.

Every agent opens one focused pull request per issue. Only the maintainer
merges, after green CI; agents never push to `master`.

## Maintainer

The maintainer is a **role**, currently executed for repository PR handoffs through
our Claude-Lite GitHub Actions backend. The owner (@maksimp6) does not merge by hand.
The role name, provider identity, and executable backend trigger are separate facts:
`@claude` is reserved for the Claude Partner Agent and must not be used as shorthand
for the repository maintainer backend.

A maintainer handoff becomes executable evidence only after the configured repository
backend trigger is posted by an author association allowed by that workflow **and** the
backend visibly acknowledges the dispatch. Trigger text alone is intent, not proof that
execution started. A handoff is complete only when it reaches a material
terminal/status transition: merged, a `BLOCKED:` comment (or changes-requested review)
with evidence, or a `DEFERRED:` comment with the next trigger. Ordinary
progress/acknowledgement comments and reactions such as 👀 are not completion. The
Observer starts the `maintainer_stall` clock only when the dispatch contains both
explicit **Maintainer** intent and executable-backend evidence; implementation or other
Claude-Lite work on a PR is not a maintainer handoff. A role-only or documentary
mention also does not start the clock. Evidence/status comments must describe trigger
names without reproducing an active literal mention unless execution is intended.

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
- GitHub Copilot reviews pull requests automatically. Do not manually request
  a Copilot review; duplicate triggers waste resources and can create redundant
  review findings. Fix or answer every automatic Copilot comment and resolve each
  thread before merging.
- Request `@codex review` exactly once for the ready-to-merge head. Add or answer
  every regression test Codex proposes. If Codex has not responded by the next
  hourly maintainer pass, Claude may continue once CI and other review gates are
  satisfied.
- After feedback fixes, do not request another Codex review and do not retrigger
  Copilot. The final corrected head is validated by required CI plus resolved
  review threads. A repeated review is allowed only when the owner explicitly asks for it.
- Merge is fail-closed. Immediately before merge, verify the pull request is
  synchronized with the current `master` (`behind master = 0`). Required
  checks must be green on the exact current PR head after that synchronization,
  not merely on an earlier head or an older base.
- If `master` advances before merge, synchronize the PR again and require the
  protected checks to pass on the new synced head. Do not reuse green checks
  from the stale head.
- After final-review fixes, do not request a second review cycle. Any new head
  still requires protected CI to pass and all review threads to remain resolved.
- Use protected auto-merge while required checks or branch-protection gates are
  still pending. If GitHub reports the pull request as already `clean` and
  refuses to enable auto-merge, merge only through the protected GitHub merge
  API with the exact current head SHA. Do not hard-code a merge method such as
  `--merge`; use a currently allowed repository method (or let the API infer it),
  and treat the actual merge endpoint result as authoritative if repository
  metadata disagrees. Never force-update, rewrite, or bypass protection on
  `master`.
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
`python tests/validate_skills.py`
`pytest -q`

Android:
`gradle --no-daemon -PaliceBuildNumber=<run> -PaliceCommitHash=<sha> :app:testDebugUnitTest :app:assembleDebug`

For security-sensitive or schema changes, add focused regression coverage and document the operational setup required outside the repository.

## Owner prohibition: deletions and reversions

The owner prohibits deletion actions and reversions throughout this repository.
This restriction supersedes earlier rollback/cleanup instructions and applies to
all agents, maintainers, automation and operational work.

- Do not delete files, branches, tags, releases, artifacts, issues/comments,
  databases, records, backups, secrets or infrastructure resources.
- Do not revert commits or PRs, roll back deployments, discard existing work,
  run destructive resets/cleanups, force-push, or rewrite published history.
- Disable automatic branch deletion after merge; retain existing work and history.
- Fix problems through forward changes on a focused branch and a protected PR.
- If a task requires a prohibited action, stop that action and report BLOCKED.
  Do not infer an exception from older approvals or ordinary merge authorization.
  Only a new explicit owner instruction can change this prohibition.
- Before merge, require the `preservation-policy` check on the exact current head.
  Do not bypass its failure or weaken protection to complete a merge.

The CI check rejects deleted repository files and explicitly marked revert PRs
or commits. It cannot detect every semantic rollback or prevent out-of-band
resource deletion. Repository administrators must also prohibit branch/tag
removal and force pushes through active GitHub rulesets with no agent bypass,
make the check required, and restrict destructive external credentials.

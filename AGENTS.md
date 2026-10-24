# Logos GitHub Agent instructions

## Repository workflow

Use the smallest independent change that satisfies an issue or subtask.

1. Inspect the issue and current `develop` HEAD. `master` is the owner's personal branch: it is not a development target and never an agent PR target.
2. Create a branch named for the issue/change.
3. Implement the smallest vertical slice.
4. Add or update deterministic regression tests.
5. Before publishing any non-RED implementation commit, run the fast `bash scripts/pre_push.sh` formatting gate from a real checkout/worktree when an authorized shell checkout is available. If no shell checkout is available, this local script is not mandatory and repository file APIs may be used to commit changes to the task branch. Explicitly report that the local gate was not run; do not claim local test success. Run focused tests separately when possible and required by the change; full suites and exact-head checks remain CI/final-handoff evidence. When the local formatting gate is run, it must finish with a clean worktree.
6. Run the relevant backend/Android checks in CI.
7. Publish the branch and open the PR yourself; do not stop at a local commit or "PR metadata".
8. Push the focused branch to `origin` and create the PR against current `develop` using `gh pr create` or the available GitHub publication tool. Verify the actual PR base is `develop` after publication.
9. If publication is blocked by missing remote, credentials, network access, or tooling, report the exact failing command/error and do not claim that a PR exists.
10. Merge only after required checks and review policy are satisfied.
11. Never push or rewrite `master` directly, never push directly to `develop` from an agent task branch, and never commit secrets. Promote `develop` to `master` only through a separate explicitly authorized integration PR.

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

## Report freshness

- Every repository report (audit, triage, escalation, handoff) must state the exact
  `develop` HEAD (full SHA) it was verified against. A report is valid only until the
  next push to `develop`; before acting on a report whose HEAD is stale, re-verify the
  affected issues and pull requests against current `develop` and mark outdated items.

## Request discipline

- One request equals one complete available cycle: inspect current state, execute,
  test, review, fix, re-verify. Do not stop after a plan and do not ask "next?" when
  the next step is safe and available. A step is safe only when it lies outside
  protected operations: merge, cutover, destructive or external actions, and
  anything else reserved for the owner in this document.
- A blocker is reported with its exact cause. Do not simulate work and do not
  fabricate results: an unexecuted step is reported as unexecuted.
- After two failed attempts with the same method, change the method; do not repeat
  a failing approach a third time.
- Exit code 0, HTTP 200, and green CI do not prove end-to-end behavior on a real
  device. Claims about user-facing behavior require device-level verification per the
  "Product acceptance" section.
- Before any device action, verify device identity, channel direction, and the
  allowed channel. Do not replace a device action with a chat link.

## Branch routing invariant

Every routine agent/task PR MUST target `develop`, never `master`. Before any PR creation or update, inspect the actual base/head and current `develop` HEAD. If an existing routine PR targets `master`, correct its base to `develop` only after reviewing the resulting diff, conflicts, and CI impact; do not blindly retarget unrelated release/integration PRs. A separate integration PR from `develop` to `master` requires explicit owner authorization and the protected release checks. Agents must not initiate, merge, or retarget such an integration PR; only the repository owner can explicitly authorize that workflow. GitHub branch protection must enforce this independently of agent instructions. `OK` acknowledges a stage only, never grants merge permission.

## Agent dispatch

Logos uses **roles first, model providers second**. The repository roles are
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
- An escalation, audit, or triage report is not a result. Every escalation closes with
  either an executed fix (a merged PR) or an explicit owner decision request. A report
  containing recommended "Actions" must name the executor for each action or request
  owner authorization; a list alone closes nothing and does not count as work.
- Process Governor must not edit application/runtime production code while acting in
  that role, expand its own authority, self-approve, or self-merge governance changes.
- Existing owner-approval boundaries remain authoritative for deployment, destructive
  data changes, secrets, CODEOWNERS, branch protection, permissions, and any expansion
  of agent authority.

### Execution backends and escalation

- `@codex` is the default repository implementation backend. Routine and mechanical
  Codex work uses **GPT-5.4 nano**; `.codex/config.toml` keeps nano/low defaults.
- `@copilot` remains the native Copilot cloud agent and automatic PR reviewer.
- `@logos` remains Logos's self-improver for its own UI, translations,
  prompts, tools and docs.
- Anthropic/Claude execution backends are not part of the repository execution
  path. Do not dispatch routine or maintainer work to Claude.

Do not create a new vendor-named agent profile when a reusable engineering role
describes the work better. Prefer adding or refining a role profile, then choose
the cheapest backend/model that can reliably perform that role.

### Cost-control policy

- For native Logos in PR #1435, the owner's quality-first direction takes
  precedence over economy defaults: use the strongest available compatible
  profile, full applicable initial context and tools, with explicit requested
  parameters and no silent downgrade. Follow `docs/agents/logos-native-quality.md`.
  Budget limits work volume, not the quality of an accepted unit of work.

- Choose the cheapest sufficient tool, backend, and model. Prefer deterministic connectors, search, or page reads when they suffice; a static fetch is not a substitute for required interactive browser verification.
- Before a new paid tool or agent run, require owner-approved task scope and budget; wallet balance is not spending permission. An expensive-model escalation, increased paid concurrency, or larger budget requires explicit owner approval unless already covered by that approval. Do not ask again for every safe step within the approved scope.
- Pass supported step, call, and time limits as actual tool arguments. For a small browser diagnosis, default to at most 10 steps and 120 seconds when supported. Size longer workflows upfront within approval; these defaults are not spending authorization.
- Track the remaining allowance across runs, continuations, and retries; stop before an action would exceed approved limits. Disclose an absent hard currency cap or uncertain billing before approval: step/time estimates are not a guarantee of the final charge. Do not silently extend the budget.
- Record the provider's `run_id` or equivalent. A timeout or delayed response must not start a duplicate paid run; inspect, poll, or continue the existing run where supported. A new attempt requires a diagnosed cause and remaining approved budget, not a blind retry loop.
- Keep one primary owner per objective and do not pay multiple agents to redo the same work. Across work you coordinate, default to one paid browser or agent run at a time; do not claim account-wide enforcement without verified shared coordination.
- Reuse evidence only while the relevant commit, inputs, environment, and authorized account remain valid. Reuse suitable sessions, traces, and artifacts rather than repeating an up-to-date paid check merely for reassurance.
- Do not enable automatic wallet reloads, recurring paid monitors, or other continuing spend without explicit owner approval.
- For external Remote Desktop Commander work, follow `docs/operations/rdc-call-budget.md`: set a task call cap, reuse valid evidence, request screenshots only on demand, and collect the existing PID/call_id with bounded delayed reads. Stop repeated deterministic failures (including Android shell EACCES); diagnose the cause before another attempt. Python orchestration uses `rdc_connection.usage_control.RdcTask` around already-authorized, traced callbacks. This helper does not authorize device actions or enforce an account-wide quota.
- Cost saving must never remove required security checks, deterministic regression tests, protected exact-head CI, or required real-scenario verification. Save money by removing redundant work, not required evidence.
- Report the outcome, blockers, and provider-confirmed spend separately from estimates; mark unavailable cost data as unknown, not zero. Include separately billed model/tool charges when known. Do not hard-code provider prices into policy; obtain current pricing when a concrete budget decision depends on it.

### Model policy

Repository automation should prefer deterministic GitHub workflows and explicit tool execution. Model-backed execution is optional, never required by policy, and must not become a merge prerequisite.

Every agent opens one focused pull request per issue. Only the maintainer
merges, after green CI; agents never push directly to `develop` or `master`.

## Maintainer

The maintainer is a **role**, not a model/provider identity. It consumes the
deterministic #496 admission evidence and performs protected GitHub merge operations.
There is no dedicated Claude maintainer backend.

A maintainer handoff becomes executable evidence only after an allowed configured
backend visibly acknowledges the dispatch. Trigger text alone is intent, not proof
that execution started. A handoff is complete only when it reaches a material
terminal/status transition: merged, a `BLOCKED:` comment (or changes-requested review)
with evidence, or a `DEFERRED:` comment with the next trigger. Ordinary
progress/acknowledgement comments and reactions are not completion. The Observer
starts the `maintainer_stall` clock only for actual executable-backend evidence.

- The maintainer merges pull requests only after exact-head CI/review/readiness
  evidence is valid and no required review thread remains open.
- Use deterministic GitHub evidence first; model review is supplementary and must not
  bypass protected admission.
- Draft pull requests are not merged; the author marks them ready first.
  Logos always opens drafts, so the maintainer marks a Logos draft ready once
  its CI is green, then reviews it like any other pull request.
- Keep a pull request in working/draft state while implementation and CI are still changing. Do not request extra model review during this phase.
- A pull request enters its single final review cycle only when the current head
  has passed required CI and there are no open review threads from earlier work.
  At that point mark it ready for review.
- GitHub Copilot reviews pull requests automatically. Do not manually request
  a Copilot review; duplicate triggers waste resources and can create redundant
  review findings. Fix or answer every automatic Copilot comment and resolve each
  thread before merging.
- The final corrected head is validated by required CI plus resolved review threads. Extra model review is optional and never a merge prerequisite.
- Merge is fail-closed. For normal task PRs, immediately before merge verify the pull request targets and is synchronized with current `develop` (`behind develop = 0`). Only an explicitly authorized integration PR may target `master`; for that PR, verify synchronization with `master`. Required
  checks must be green on the exact current PR head after that synchronization,
  not merely on an earlier head or an older base.
- If the PR's actual base branch advances before merge, synchronize the PR again and require the protected checks to pass on the new synced head. Do not reuse green checks from the stale head.
- After final-review fixes, do not request a second review cycle. Any new head
  still requires protected CI to pass and all review threads to remain resolved.
- Use protected auto-merge while required checks or branch-protection gates are
  still pending. If GitHub reports the pull request as already `clean` and
  refuses to enable auto-merge, merge only through the protected GitHub merge
  API with the exact current head SHA. Do not hard-code a merge method such as
  `--merge`; use a currently allowed repository method (or let the API infer it),
  and treat the actual merge endpoint result as authoritative if repository
  metadata disagrees. Never force-update, rewrite, or bypass protection on
  `develop` or `master`. An `OK`/stage acceptance is not merge authorization.
- The owner's explicit approval is still required for production deployments,
  database migrations that change or drop existing data, and changes to
  secrets, CODEOWNERS or branch protection.

## Owner norms

- The owner's CI reporting norm — a CI job that runs over 30 seconds is reported as
  policy PASS — is an owner decision, implemented by repository CI policy. Its scope
  is strictly CI job/check reporting: a green policy status means that job-level task
  is completed per the owner's definition. Proof is physics and logs, not
  re-litigation of the norm.
- Scope boundary: this norm defines job-level completion only. It does not define
  product acceptance. The "Product acceptance" section remains the sole completion
  standard for user-facing artifacts. A job-level PASS is evidence that a job finished,
  never evidence that an artifact is fit for the owner.
- This norm is not subject to review, reinterpretation, or change proposals by any
  agent, role, automated reviewer, or governance process. Do not open issues, pull
  requests, or escalations that question or modify it; only the owner changes it.

## Architecture rules

- Treat `UniversalToolExecutor` as the execution boundary for tool calls.
- Preserve `InvocationContext` and `ExecutionTrace` correlation across requests, tool calls, polling, and continuations.
- Keep secrets out of logs, traces, issues, tests, and client-visible errors.
- Prefer repository-local web UI assets. Do not introduce CDN-hosted UI dependencies without an explicit issue.
- Android changes must preserve system-bar safety and stable debug/release signing boundaries.
- The accepted persistence engine is native `kristall.store.Kristall` with one writer per journal path. Do not introduce SQL, SQLite, PostgreSQL, SQL migration code, alternative storage engines, or compatibility adapters.
- Durable data changes must use native Kristall transactions and verified backup/restore. Keep SQL-free acceptance tests fail-closed until every runtime SQL consumer has been removed.
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

## Product acceptance

- For any change that affects user-facing behavior (UI, application flows, installer, updater), the artifact itself is part of the evidence. `assembleDebug` proves a build exists; it does not prove the artifact is fit for the owner's goal.
- Before any artifact is handed to the owner for installation, the assigned agent must (a) verify artifact identity (digest/SHA against the recorded reference) and (b) inspect the artifact contents (application id/package, entry point, embedded UI assets or screens) and report both results.
- A user-facing change is complete only after device-level verification: installed and accepted on the target device by the owner or a verified executor. Green CI is not completion for user-facing changes.
- A report that hands an artifact or update to the owner must state the exact build (commit, run id, artifact digest) and the inspection result.

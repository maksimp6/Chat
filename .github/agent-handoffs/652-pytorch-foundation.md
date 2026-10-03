# PyTorch implementation handoff

Issue: #652
Contract PR: #657
Task ID: pytorch-foundation-implementation-v1
Stage: implementation
Primary role: Backend Engineer
Backend: Claude Direct, fresh implementation session
Branch: impl/652-pytorch-foundation
Target: master, protected merge only
Coordination: #652 and shared work-coordinator epic #576

## Accepted contract and independence

Accepted contract head: fddb2d1fa0a861edb2b79e908576531e7899dd2d
Reviewed base: 9991b057e8c54e32e06738c23ca2d128b672bdb7
Contract reviewer run: 36778304236
Acceptance: https://github.com/maksimp6/Chat/pull/657#issuecomment-5919881986

The ChatGPT Test Engineer authored the tests. A separate Claude Direct reviewer
accepted them. This handoff is coordination metadata, not implementation or
solution approval. The Backend Engineer must use a new implementation session,
not continue the reviewer's conversation. Latest ownership/run/CI evidence lives
in the issue and implementation PR; do not infer progress from this static file.

Keep both accepted test blobs byte-for-byte unchanged:

- tests/test_pytorch_adapter_contract.py: fb8e708453b1086b579fb82534da8b9483e80630
- tests/pytorch_cpu_integration.py: df97cc12d19c3b19292e7803ab176a25efbde802

Additional regressions belong in separate files. If a contract is genuinely wrong,
report the contradiction for independent review instead of weakening it.

## Deliverable

Implement the smallest complete optional CPU-first PyTorch foundation in this
branch and publish the result to its draft PR. Yandex remains the primary AI
provider. A fixed tensor smoke operation is not a trained model.

- Preserve PyTorchAdapter(enabled=False, importer=None), status(), smoke(), and
  register_pytorch_tools(registry, adapter=adapter) from the accepted contract.
- Use ToolRegistry -> UniversalToolExecutor, with production discovery and real
  tools.execute/MCP-path regression coverage, not only manual test registration.
- Preserve runtime_id, InvocationContext and ExecutionTrace; the reviewer requires
  call.metadata runtime_id to survive in result metadata. Runtime-managed access
  must pass through RuntimeDispatcher and fail closed across runtime/owner scopes.
- Lazy disabled/missing/unavailable/ready states must not break normal startup.
  Redact import and computation errors from both results and traces.
- Only the fixed tiny CPU float32 computation is allowed. Reject extra payloads
  before framework work. Do not mutate process-global PyTorch configuration.
- Add a separately installed, exactly pinned CPU-only profile compatible with the
  existing Python 3.14 CI. Verify the actual official wheel, license, dependencies
  and security metadata. Do not reuse an earlier speculative pin as verification.
- Add a narrow cached CPU integration job that explicitly executes
  python -m pytest -q tests/pytorch_cpu_integration.py and fails on a missing or
  broken dependency. Prefer a dedicated ML workflow over editing shared CI.
- Keep torch out of default requirements, setup/maintenance, Android and desktop
  packaging profiles. No source builds, TensorFlow, torchvision or torchaudio.
- Document opt-in, installation, supported platform, disable/rollback, missing
  dependency behavior, fixed smoke example and the limits of this foundation.

## Cross-chat file boundaries

- #653 / #658 owns the Sber contract and banking integration. Do not touch its
  tests, credentials, banking code or Treasury semantics. Coordinate any future
  shared registry/runtime edit in #652 before parallel authorship.
- #646 / #656 owns local_agent_gateway.py, local_agents/ and result-bridge tests.
  Do not change remote result polling or serialize live trace objects there.
- #576 / #580 / #655 and #634 / #637 own coordinator, task orchestration, agent
  dispatch and skill policies. Do not edit their files or depend on merging them.
- #650 / #651 owns Android root-agent implementation. No ML packaging on Android.
- Avoid shared executor/registry refactors. Make only the minimum additive wiring
  needed for this feature, with production-path regressions. Re-check active PRs
  before any shared-file edit; report an overlap instead of overwriting it.

## Validation and publication

Read AGENTS.md, #604, .agents/skills/issue-to-pr/SKILL.md,
.agents/skills/github-pr-readiness/SKILL.md and runtime-dispatcher policy first.
Respect repository layout rules; do not weaken validators to permit a new module.
Use only bash scripts/format.sh write and bash scripts/format.sh check.
Run focused contract and discovery tests, then required backend/PostgreSQL/Android
CI. Verify the two accepted blob hashes after implementation. Record real CPU
installation/import/test evidence separately from mocked tests.

Do not merge the RED contract branch into master. This implementation branch
inherits it and must reach full GREEN before protected maintainer review/merge.
Keep the PR draft; no manual Copilot request, final Codex review or self-approval.
Return exact head/base, changed files, commands/results, blockers, run/session and
artifact references, plus a short advisory opinion. Never label pending checks as
passed. The next stage after implementation is independent verification and
solution review, not automatic merge.

## Cost and permission boundaries

One active task/owner for #652. No parallel helper, duplicate dispatch or blind
retry. Preserve existing worker model, turn and permission limits. The workflow's
actual configuration overrides the human-readable Lite name for cost reporting.
Use already-authorized editing/publication tools. If access or publication is
denied, report the exact operation and stop; do not circumvent tool restrictions.
No new credentials, branch-protection/CODEOWNERS changes, production deployment,
database migration, model/checkpoint download, arbitrary code/model loading,
training, GPU provisioning or paid inference fallback.

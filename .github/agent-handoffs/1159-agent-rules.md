# #1159 — Agent Rules Registry

- Canonical contract: owner message 2026-10-11 and Issue #1159, AR-001–AR-006.
- Stage/owner: candidate implementation; ChatGPT, Backend/Test responsibilities.
- Base: master 51aaf8273f4f465fe78aa0a79e76a22d8f141e35.
- Branch: feat/agent-rules-registry. Expected output: focused Draft PR with registry,
  host-controlled task adapter, ExecutionTrace events, deterministic regressions.
- Actual capability: local isolated Git worktree/Python tests, Git push, GitHub connector.
- Scope authorized: code/tests/docs publication. No production activation, device action,
  deployment, protection/CODEOWNERS changes or merge.
- Stop conditions: changed scope or head, absent trusted adapter observations/permissions,
  failed format/required CI, attempted self-activation or live rollout without acceptance.
- Tests-first: collection failed because proposed controller did not exist. Implementation
  then exercised negative dispatch/receipt/route/retry checks and real Trace correlation.
- Handoff: independent review and adapter rollout are pending. Current task-instance
  controller is not installed globally. See docs/agents/agent-rules-registry.md for limits.

## Local validation

- 33 focused controller/executor tests pass; controller 100% statement/branch coverage.
- 66 Code rules tests pass; new package locked into STRICT_CLEAN_FILES. No baseline increased.
- Runtime/skills validators and compile check pass.
- Full local pytest is BLOCKED at collection: missing python-dotenv and pytest-asyncio
  in this shell environment (21 collection errors). Hosted exact-head CI must supply final evidence.
- Broad exception handling is localized to a host callback boundary, with one explained
  BLE001 suppression so callback errors fail closed without logging secret values.

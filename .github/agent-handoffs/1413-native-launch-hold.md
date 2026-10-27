# #1413 D0 — native launch hold

Historical implementation handoff, 23 October 2026. The branches, workflow
inventory and test claims below belong to that original scope and revision;
they are not a current inventory or permission to repeat tests. For new work,
follow [AGENTS.md](../../AGENTS.md), the [current D0 matrix](../../docs/agents/logos-native-quality.md#generation-permission-court-decision-d0)
and [court procedure](../../docs/agents/logos-court-procedure.md). Publication is
directly to `develop`; tests are `NOT_RUN` absent a separate owner instruction.
The launch hold remains in force.

Accepted scope: owner's 2026-10-23 instruction to fix the court defects;
[court remand D0](https://github.com/maksimp6/Chat/issues/1413#issuecomment-6366889282),
[existing admission](https://github.com/maksimp6/Chat/issues/1413#issuecomment-6368155981).
Primary implementation owner: root/native_hold, Infra Engineer.
Independent reviewer: root/native_hold/hold_review, read-only Test/Security review.
Base develop: `2e1f1bcacfe6f4762b32c3e2e5b1114448199cc3`.
Branch: `fix/1413-native-launch-hold`. Publication is coordinated by root.

## Contract and capability

Enforce the existing court prohibition before a new generation, continuation,
native tool session or related model-backed test starts. Use the real local
checkout, offline deterministic tests and normal protected PR workflow. Model
budget is zero. No provider/model calls, workflow dispatches, live server/device
actions, permission/credential changes, merge or deployment are authorized.
There is no environment, workflow-input or CLI switch to lift this hold; a future
separately authorized code change must meet the court's return conditions.

## Entry inventory and implementation

| Entry | Trigger / path | Enforcement |
| --- | --- | --- |
| `logos.yml` | Trusted Issue open/mention/comment, `logos` label; retries rerun this job | Mandatory early preflight after checkout, before credentials; `run_issue_task` also denies before `app`, files, invocation or client initialization. |
| `rag-work.yml`, `mode=logos` | Manual native dialogue | Early mode-specific preflight before context/upload/runtime; `run_dialogue` and `_ask` guard before native generation/tool loop. |
| `semantic-work.yml` | Approved query/red/vector model branch; historical repository model demo | Early model-branch preflight; semantic CLI, direct vector model demo and `yandex_call` guard before external model work. |
| `launch-smoke.yml`, `live_provider=true` | Optional real provider test | Unconditional first live-job failure before protected-master checkout; runtime full smoke denies before credentials/server. Offline job is unchanged. |
| `logos-response`, `logos-result` | Existing provider response GET or saved Actions artifact read | Remain available, no generation guard. Existing transport tests require zero POST/generation requests. |
| `logos-tools`, BM25 search/selftest, offline launch smoke | Deterministic audits/checks | Remain available. No native generation is introduced. |

The common guard is `agent_runtime/logos_launch_hold.py`, using only the standard
library. Denial is an explicit failed/blocked result, never a completed review.
The live-smoke workflow preflight is self-contained because its protected-master
checkout may not yet contain the new guard module. Its ref, environment and
permissions are unchanged.

`logos-awakening.yml` emits a scheduled signal; it does not call a model. Its
schedule and permissions were not changed. `rag_pipeline.yandex_embeddings` is
a latent optional function, not invoked by its current CLI. Ordinary application
UI/HTTP routes, `agent_runtime.cli`, `crl.sh` and `test_api.sh` are outside this
native court-runner change; the shared Yandex client is not globally disabled.

## Evidence and boundaries

- 212 focused worker/dialogue/workflow/semantic/smoke tests passed locally.
- 109 code-rule checks passed; reliability baselines were not weakened.
- Worker and common hold module: 105/105 statements and all 8 branches covered.
- Independent reviewer reran 31 hold/smoke tests and inspected production order;
  no blocking findings on the reviewed diff.
- Negative control: replacing the common guard with a no-op **only in one test
  process** made the native-ask test fail at the forbidden conversation sentinel.
  No source file or provider was changed by this control.
- Existing fake-only unit modules explicitly substitute the guard while their
  external transports remain synthetic. New production-hold tests do not make
  that substitution. A spoofed approval environment cannot lift the hold.
- Saved reader, full provider GET, deterministic semantic selftest and direct
  script CLI remain covered. These results do not prove any live model behavior.
- Formatting gate, code rules, final commit and exact-head CI are recorded by
  the publishing coordinator after this change is committed.

Stop if the branch/head changes concurrently, model spending/new authority is
needed, or a protected operation is required. The hold is prepared code until
merged through the owner workflow. Already running jobs and reruns of old refs
cannot be retroactively intercepted by new code; doing so would require a
separate administrative action. This change does not claim account-wide blocking,
owner acceptance or permission to merge, and does not lift the court remand.

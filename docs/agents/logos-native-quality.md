# Native Logos: quality and complete case context

## Current scope — 27 October 2026

[AGENTS.md](../../AGENTS.md) and the owner's current direct instructions govern
new work: publish by direct push to `develop`; do not create a PR or merge.
Tests are `NOT_RUN` unless separately authorized, and authorized tests run only
in a GitHub Actions runner. Publishing a correction grants no model, test,
credential, deployment or merge permission. Historical admissions below do not
renew themselves. Apply rules in force at the event date when examining old work.
The current [court procedure](logos-court-procedure.md) keeps allegations,
evidence, independent verification and owner acceptance separate.

Owner update, 23 October 2026: the requested replacement configuration is the
[Pro 5.1 async profile](logos-pro51-async-profile.md), with a requested
32768-token output budget and no silent downgrade. That artifact is prepared,
not activated; its transport and model limitations are explicit. The existing
Responses runtime below remains unchanged until a separately reviewed integration.

Canonical task: #1413. Implementation: PR #1435, `feat/semantic-pipeline`.
Accepted owner direction, 22 October 2026:

> ты здесь чтобы это исправить и исполнить мою волю, дать все возможное логосу без удержек, все параметры самые высокие и контекст. модель выбери самую сильную, я склоняюсь что qwen мощный, но ты суди сам

The preceding owner request was to read this PR and all its comments. The
coordinator read the description, relevant source and all 24 discussion entries,
including the quality-first requirement and its later corrections. The full
current GitHub case is fetched again before generation; this paragraph is not a
replacement for that discussion. The earlier ChatGPT archive is not available to
this checkout and must not be claimed as embedded.

## Archived implementation admission — 22 October 2026

The following admission records the original PR #1435 scope and revisions. It is
historical context, not permission to open or continue a PR, run verification or
activate the profile today. Current publication follows AGENTS.md.

The original task authorized implementation and verification in the existing PR.
Coordinator: Codex. Independent owners: backend tool contracts, runtime
continuation, initial context package and existing knowledge attachment. The
initial branch head is `703214b89855f6a7ea9d916ae00a9561e5674e44`; verified develop
is `1db3ad8ac3ac09f7e435f068d728dbcd8b989ba7`. No second implementation or PR.
Protected publication and deployment still require their existing authorization;
owner stage acceptance does not grant merge permission.

## Historical requested native profile — 22 October 2026

Use `deepseek-v4.1-flash/latest` in the common Yandex instance, with its documented
1,048,576-token context. The current Yandex catalog recommends it for maximum
capabilities; Qwen3.6 35B has 262,144 tokens and is recommended for speed. Qwen3
235B ceased being supported on 30 September 2026. This selection follows the
available platform's current recommendation; it does not claim superiority on
every task or silently substitute a subscription endpoint.

Request `reasoning.effort=high`, the highest level documented by Yandex, and
`max_output_tokens=393216`, the ceiling published by DeepSeek upstream. The
latter is an explicit compatibility candidate: Yandex's accepted ceiling has not
been documented. Unsupported values fail visibly; no automatic reduction or
weaker-model fallback. Receipt fields distinguish requested, echoed/accepted and
effective/unknown settings. Temperature 0.3 follows Yandex's engineering example;
increasing randomness is not increasing intelligence. Do not invent a numerical
maximum for hosted calls where the provider publishes none.

Hosted web search uses high search context; file search uses the existing
repository Vector Store plus the full current case; Code Interpreter is enabled.
Every registered local category is available through the existing executor and
its existing approval rules. Availability in the request is not proof of every
external dependency or tool's successful execution.

The run has no approver, so it uses `approval_mode=deny`: one approval-gated call
no longer stops its whole batch. The other calls execute, each gated call is
refused by the executor (never auto-approved) and the model is told it was not
executed; the result lists such tools in `approval_refused_tools`. Tools the
runner cannot execute are not offered: MCP-only bridge tools and Termux:API tools
without their Android helper. Git tools without an explicit repository use this
checkout. Partner and 3D tools still require a trusted owner identity, which an
unattended run does not have; they fail closed with that reason.

## Context and continuation

Before the first model response, load AGENTS.md when present, the applicable admission
document, the Backend Engineer role and its three skills, owner context, canonical
issue, PR description, every case comment, review and inline reply. Record source
version, hashes, redactions and complete pagination. Missing context fails
preparation. The input is not clipped; `truncation=disabled` prevents automatic
loss of early conversation at the provider. Overflow is an explicit failure.
Additional sources remain accessible through the existing checkout, file search,
HTTP and web search; the coverage manifest names unembedded materials.
Two native project tools read repository REST sources (including Actions logs)
and Discussions with full comment/reply pagination. They use the existing job's
read permissions; their transport token is never put in prompts, tool arguments,
or exported receipts. This is scoped authenticated reading, not a claim of
process isolation from arbitrary shell execution.
Manual dispatch can select a canonical issue and PR. Reference-case mode records
that PR's actual historical revision separately from the current checkout, so a
dialogue on develop does not falsely claim to execute the old PR revision.

The request runs in background mode with a stored response ID, 900-second POST
transport allowance and up to 7,200 seconds of GET polling per response. Native
continuation has no adapter round-count cap. The Actions hosted job has its
platform maximum of 360 minutes. These are execution boundaries, not a claimed
maximum number of function calls. A timeout never starts a duplicate POST.

Preserve the known conversation/response IDs, requested parameters, context
manifest and tool trace in the result artifact even on failure. An unfinished
response can be inspected by ID without a new generation. This is not a claim
that a completed Actions job can restart itself or that approval is automatic.
The known response ID is checkpointed before long polling. A job interruption
therefore leaves an inspectable response identity rather than inviting a duplicate
generation. Captured source patches include the current dialogue, context,
knowledge, quality and runtime files in addition to the original tool-repair
paths; changes outside those explicit paths remain names-only for review.

## Generation permission (court decision D0)

D0 remains in force under the [owner's 23 October decision](https://github.com/maksimp6/Chat/issues/1413#issuecomment-6366889282).
The following is a source inspection at
[`5a5112ca8a62e33bb589404297f592055ec18a72`](https://github.com/maksimp6/Chat/tree/5a5112ca8a62e33bb589404297f592055ec18a72),
not a test result or proof of the code installed on a device.

| Entry at that revision | Actual control before model work | Variable / scope |
| --- | --- | --- |
| `agent_runtime.issue_runner.run_issue_task` | Calls `enforce_native_launch_hold()` before context, app or client initialization; returns failed / `NativeLogosLaunchBlocked`. | No variable override. |
| `scripts.logos_dialogue.run_dialogue` and `_ask` | Both call the hold; `_ask` checks before conversation creation. | Question generation is held; `--response-id` reads a stored response through `inspect_response`. |
| `scripts.logos_once.run_native` | Calls the hold before native dependency/context/provider work; returns blocked with zero generation requests. | Its later compatibility gate cannot bypass the hold. |
| `scripts.semantic_pipeline.yandex_call`, model branches in `_run`, and model-enabled `run_vector_store_demo` | Call the hold before the requested model operation. | Deterministic retrieval is a separate path; external index/upload permissions remain separate. |
| `scripts.local_launch_smoke.run_smoke` | `_admit_smoke_mode(offline=False)` calls the hold before live smoke setup. | Offline mode is not automatically authorized: test admission still follows AGENTS.md. |
| `agent_runtime.logos_launch_hold.enforce_native_launch_hold` | Always raises `NativeLogosLaunchBlocked`. | No environment, CLI or workflow-input override exists. |
| `agent_runtime.generation_gate.generation_allowed` | Retired compatibility function: always returns `True`. | `LOGOS_GENERATION_ALLOWED`, including `true`, does not lift D0 or control this function. |
| Historical `logos.yml`, `rag-work.yml`, `semantic-work.yml`, `launch-smoke.yml` | These workflow files are absent at the inspected revision. | Old runs/refs retain their historical code; this inventory does not establish account-wide blocking. |

The common guard is not a global interceptor for every Yandex client or
application HTTP route. In particular, this table does not prove that
`/api/chat`, voice routes or arbitrary code cannot call a provider. Existing
stored-result readers remain separate from generation; availability of a reader
does not grant permission to transmit private data or create external writes.
The historical [D0 handoff](../../.github/agent-handoffs/1413-native-launch-hold.md)
records its own revision and scope; its old workflow inventory is not today's
entry list. Changes in this correction, including the PR court preflight, must
be assessed at their resulting commit separately from the snapshot above.

This correction adds the same unconditional guard to `scripts.pr_court.main`:
it can collect GitHub evidence and save an offline review plan, then reports
`HELD` before reading provider credentials or making a model request. Invalid
or oversized evidence is reported separately. This ordering is statically
reviewed; its runtime tests are `NOT_RUN`.

### One route for a future owner-authorized D0 amendment

`HELD → offline evidence → owner authorization → controlled live verification → owner acceptance`

1. While `HELD`, prepare a versioned entry inventory, saved evidence, static
   review and a list of unresolved criteria without generating or continuing a
   response. Deterministic tests, including negative controls, remain `NOT_RUN`
   until separately authorized for a GitHub runner. A live-only criterion is
   `BLOCKED / NOT_RUN`, not a failed prerequisite that requires an illegal run.
2. Submit the offline record and the exact unresolved live criteria in canonical
   case #1413. Record the owner's explicit decision there with its source, including
   a direct instruction received in chat. Only that owner decision may amend D0
   for a bounded live verification: identify revision, operator, allowed entry, model/data,
   operation and cost scope, stop condition, and what remains prohibited. If the
   existing “all remarks first” wording blocks a live-only criterion, that
   decision must expressly change its order; the coordinator cannot infer an
   exception from a request to publish, review or continue coding.
3. The decision must separately authorize the required code change to the
   unconditional hold. Publish that scoped change according to current
   AGENTS.md and obtain independent review; no environment toggle substitutes
   for it. This document makes no such change and grants no such admission.
4. Execute only the admitted live verification, record actual requests, tool
   results, postconditions and limits, and stop at the authorized boundary.
   An unsuccessful or unavailable verification remains open with its evidence;
   HTTP 200 or CI success cannot lift the remaining hold.
5. The owner separately accepts or rejects the result in #1413. Verification,
   owner acceptance and any broader generation/deploy/merge permission remain
   distinct decisions. A scoped experiment does not authorize normal operation.

## Evidence and acceptance

These are evidence criteria, not permission to execute them. Tests remain
`NOT_RUN` until separately authorized in a GitHub runner. Once admitted, offline
checks must exercise the actual outbound request, full input and schemas;
negative controls cover omitted context, schema defects, late approval and
timeout. Only after the explicit D0 amendment above, controlled live verification
must show actual hosted-tool results, local-tool postconditions and an unprompted
answer about the supplied case. A capability
receipt or green CI alone is not live verification. Provider-confirmed spending
remains unknown unless a bill is returned; token estimates exclude separately
billed hosted-tool and session charges. Quality is never reduced to fit a budget:
stop the next unit of work explicitly when its authorized volume is exhausted.

Sources checked 22 October 2026:

- https://aistudio.yandex.ru/ru/docs/ai-studio/concepts/generation/models
- https://aistudio.yandex.ru/ru/docs/ai-studio/release-notes/
- https://aistudio.yandex.ru/ru/docs/ai-studio/concepts/generation/chain-of-thought
- https://aistudio.yandex.ru/ru/docs/ai-studio/operations/agents/use-code-interpreter
- https://aistudio.yandex.ru/ru/docs/ai-studio/concepts/limits
- https://aistudio.yandex.ru/ru/docs/ai-studio/pricing
- https://api-docs.deepseek.com/api/list-models/

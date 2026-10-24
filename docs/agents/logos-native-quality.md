# Native Logos: quality and complete case context

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

## Accepted implementation and responsibility

The current task authorizes implementation and verification in the existing PR.
Coordinator: Codex. Independent owners: backend tool contracts, runtime
continuation, initial context package and existing knowledge attachment. The
initial branch head is `703214b89855f6a7ea9d916ae00a9561e5674e44`; verified develop
is `1db3ad8ac3ac09f7e435f068d728dbcd8b989ba7`. No second implementation or PR.
Protected publication and deployment still require their existing authorization;
owner stage acceptance does not grant merge permission.

## Selected native profile

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

Before the first model response, load full AGENTS.md, the applicable admission
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

The court decision in #1413 keeps native generation closed until the owner
explicitly allows it. The technical control is the repository variable
`LOGOS_GENERATION_ALLOWED` (Settings → Secrets and variables → Actions →
Variables). Only the value `true` opens generation; an absent or any other value
keeps it closed. Actions compares this value ignoring case, and so does the code.

- `logos.yml` does not start its job for any trigger: comment, new issue or
  label. The existing trigger rules still apply when the variable is `true`.
- In `rag-work.yml`, `mode=logos` (context capture, knowledge attachment and the
  dialogue) does not start. `logos-response` (reading a stored response without
  generation), `logos-tools` and `logos-result` stay available.
- `agent_runtime.issue_runner` and the question mode of `scripts.logos_dialogue`
  check the same variable before reading context, seeding credentials or creating
  a client. A refusal writes `status=blocked` and `error=GenerationNotPermitted`,
  makes no model request and exits with 1.

This control only covers workflow and code versions that contain it. A re-run of
an earlier run uses that run's original commit, and a manual dispatch on an older
ref uses that ref's workflow file. Neither is closed by this change. Closing them
needs owner-level measures: restricting or moving the `YANDEX_API_KEY` secret,
disabling the workflows, or limiting the key at the provider. Server chat
endpoints (`/api/chat`, `/api/mcp/execute-approved`, `/api/voice/*`) are not
affected; whether the decision covers them is the owner's call. The inventory of
entry points is recorded in #1413.

## Evidence and acceptance

Offline tests must exercise the actual outbound request, full input and schemas;
negative controls cover omitted context, schema defects, late approval and
timeout. Live acceptance must show actual hosted-tool results, local-tool
postconditions and an unprompted answer about the supplied case. A capability
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

# #1413 D9 / D10 — preserve result and refusal diagnostics

Canonical task: #1413; existing implementation PR: #1441.
Accepted contract: owner's 2026-10-23 instruction to fix the court defects,
[D9/D10 remand](https://github.com/maksimp6/Chat/issues/1413#issuecomment-6366889282),
[current admission](https://github.com/maksimp6/Chat/issues/1413#issuecomment-6368155981).
Base develop: `2e1f1bcacfe6f4762b32c3e2e5b1114448199cc3`.
Starting PR head: `84546ad801f6a24579aa4aac22e3f90ee47fa0d2`.
Branch: `fix/logos-auth-reporting`. Primary owner: root / Backend-Infra Engineer.
Independent reviewer: root/tool_security_review, read-only Security Reviewer.

## Result and acceptance

Preserve full assistant text and exact response identity independently of a
Kristall/publisher failure. Export only machine diagnostics, excluding raw error
bodies, reasoning and tool/user messages. A refusal is available evidence, not a
completed review. `no_changes` describes the Git diff only.

This follow-up to the existing delivery fix retains incomplete reasons (including
`content_filter`), non-null error presence, safe code/type and provider parameter
echoes. Each response keeps its own machine-state receipt; a later response cannot
inherit an earlier refusal. Failed polls can raise before the normal response
callback, so finalization recovers their machine state from the existing trace
without replacing the exact saved text with a sanitized/truncated trace copy.

Native dialogue and saved-response GET share the safe diagnostics implementation.
Dialogue now preserves exact Unicode/whitespace too, checkpoints received text
before persistence, excludes non-assistant output, and rejects non-null provider
errors before a completed/approval result. Earlier text receipts remain available
if a continuation has not yet produced text.

The isolated workflow renderer includes safe reason/code/type, excludes arbitrary
provider messages, and cannot report success for incomplete/non-null-error results.
Publication stays in the same PR; this is not a second delivery implementation.

## Evidence and boundaries

- Focused worker, dialogue and workflow tests cover long Unicode/whitespace,
  refusal, continuation, failed polling, persistence, publisher and secret-like
  diagnostics. Exact final counts, negative controls, formatting and CI are
  recorded by root in #1413/#1441 after the final commit.
- Independent review found dialogue false success on non-null errors, whitespace
  loss, missing failed-poll reason and an unsafe model-token prefix. All received
  regression coverage before handoff.
- Provider echoes are not proof of effective internal parameters or provider
  maxima. Unknown billing, effective settings and exact underlying refusal rule
  remain unknown; `content_filter` alone does not identify a responsible party.
- No native/provider generation, continuation, workflow dispatch, credential or
  permission changes, merge or deployment. Ordinary PR CI is allowed.
- Live App/PAT delivery and Android opening were not exercised here. Existing
  restoration of response `63a39a54-527a-44c4-af15-4946468e6e87` is recorded in the
  court as a separate historical fact; no duplicate retrieval was performed.
- Owner acceptance is separate and does not authorize merge. D9 runtime acceptance
  and the broader D10 conditions remain open until their specific evidence exists.

Stop on a concurrent branch shift, required new access/spend or protected action.

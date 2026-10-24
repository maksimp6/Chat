# #1413: owner-selected YandexGPT Pro 5.1 async profile

Canonical case: #1413. Accepted contract: the owner's direct request on
23 October 2026, "асинхронную модель pro 5.1 вместо текущей, создай готовый
профиль настроек и зафиксируй на суде", followed by the explicit correction
that capability limits must be maximal, not an arbitrary 8192-token budget.

Primary owner: root / Docs Engineer. Stage: prepare a configuration artifact,
validate it offline, independent review, publish a focused PR and record the
selection in the existing Court issue. Base develop:
`d04286047d1f9a5b2e310d6724cdbe8c25902c66`.
Branch: `docs/1413-pro51-async-profile`.

Admission: AGENTS.md, docs/agents/role-based-agent-office.md and docs-sync read.
Available tools: isolated public Git checkout, local JSON validation/formatter,
GitHub branch/tree/commit/PR tools and the explicitly requested Court comment.
Independent documentation review: yandex_api_docs; closing review: payload_review.

Scope: a ready HTTP configuration template for Text Generation Async, an exact
rendering/polling guide, and a pointer from the existing native-quality guide.
The chosen request is Pro 5.1 with maxTokens=32768, matching its documented
context-window size as a requested budget, NOT a verified output ceiling.
The owner rejected 8192; no automatic output-limit/model downgrade is allowed.
The model-specific release says Pro 5.1 has no reasoning mode. Do not claim the
generic ENABLED_HIDDEN flag works on this model. Explicitly retain that loss and
the async tools limitation in the profile and Court record.

No runtime adapter, production default, workflow, secret, access or generation
gate is changed. The artifact is not loaded by the existing Responses client.
Provider/model budget: zero calls. No merge, cutover or paid acceptance run.
Stop on a changed branch owner/head, missing publication permission, or any step
requiring model/provider access. Recheck develop and PR base before publication.

Acceptance: JSON round-trips quoted/Unicode input without shell substitution;
only documented async fields reach the body; the request identifies Pro 5.1,
maxTokens is 32768 as a string, tools/reasoning_effort/service_tier/Responses-only
fields are absent; Operation ID and terminal-status handling are described;
no automatic POST retry/fallback or hidden context truncation is promised.
Offline validation and exact published tree are reported in the PR. Provider
acceptance, actual output ceiling, savings and owner acceptance remain separate.

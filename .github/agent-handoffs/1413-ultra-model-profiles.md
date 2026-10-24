# #1413 — Ultra profiles: all Yandex model surfaces

Owner request, 24 October 2026: collect Ultra profiles, clarified as “all models”,
then “Yandex only”. Canonical admission:
https://github.com/maksimp6/Chat/issues/1413#issuecomment-6381606230

Verified develop: `8d0fb443f881cceb31d8703aa5d72ad0ffc68705`.
Branch: `feat/1413-ultra-model-profiles`; existing Draft PR #1572, base `develop`.
Prior branch HEAD: `9e5426483a1417fd7c2c74f4670cba50cca4111b`.
Primary owner: root / Backend Engineer. Stage: offline artifact implementation,
independent source/contract review, corrections and publication on the same PR.
Backend capability: source snapshot, offline Python tests, GitHub file/Git APIs.

## Accepted scope and admission

The local ALL_MODELS registry is not the complete Yandex inventory. Expand the
artifact to public AI Studio model catalogs and SpeechKit model/version selectors.
Include other developers' models only through their Yandex-hosted deployment.
Keep model identity, transport, documented limits, unknowns and lifecycle explicit.
Preserve the original 14 registry plans and full native/Pro5.1 variants.

Expected output: a provider-wide catalog, deterministic validator and tests,
source-backed documentation and an updated downloadable collection. Scope does
not activate profiles, change runtime consumers, authorize generation, inspect
credentials, create services, merge or deploy. Provider calls and account-specific
inventory remain NOT_RUN/NOT_CHECKED. Stop before unsupported identifiers,
invented limits, paid provider requests or protected operations.

Independent Docs Engineer work covered (1) dedicated/batch inventory and review,
(2) Realtime/images/SpeechKit and (3) embeddings/classifiers and review. Source
reads are public; these roles were not dispatched to a paid Yandex model.

## Result and exact scope

- 70 records: 64 public model/deployment/version profiles, 3 retired historical
  profiles and 3 account-specific templates. This is not 70 unique live models.
- Public surfaces: 10 base text, 3 base Realtime, 1 image, 4 embeddings,
  2 prompt classifiers, 21 batch text, 8 batch multimodal, 5 dedicated,
  6 SpeechKit STT selectors and 4 SpeechKit TTS selectors.
- The 34 batch/dedicated profiles are blocked_service_disabled, following the
  documented 10 October shutdown. Account templates are not discovered models.
- Documented source conflicts remain explicit. Embedding input is unresolved
  (8192 versus 2048); v2 dimension choice, tuned URI conflict and batch dataset
  type discrepancy retain evidence and execution blockers.
- Exact URI suffixes, transport-specific contexts and typed request fields are
  preserved. Image quality=high is unsupported; LiveTTS speed and LLM fields on
  TTS-only/embedding/classifier requests are rejected.
- Native DeepSeek and Pro5.1 async candidates remain compatibility candidates,
  not proven provider maxima. No fallback, truncation or automatic paid retry.

## Verification and review corrections

Offline source-snapshot checks: provider-wide validator PASS; 41 focused unittest
cases PASS (18 original registry cases plus 23 expanded-catalog cases). No live
model call was used. Logs are included in the downloadable collection.

Independent review found validator coverage gaps, now fixed and regression-checked:
required conflict evidence, REST embedding dimensions/URI/endpoint, classifier
limits, tuned-model unknowns, immutable forbidden request fields, and consistency
between the common record and typed plan. Negative tests reject fabricated
dedicated IDs, disabled-service activation and model/status/URI disagreement.
The disabled-service negative test now mutates both record layers so it exercises
that rule after the earlier consistency check. SpeechKit tests cover deferred
STT transport and unsafe TTS mode.

Fresh develop tree has no collision with the nine PR paths. Runtime registry,
native-quality source and existing Pro5.1 config blobs are unchanged from the
tested snapshot. Publication overlays only the nine artifact paths on this
develop tree and preserves both existing PR and develop ancestry.

## Environment and remaining gates

Shell git transport is unavailable (browser-proxy port 8889 connection failure).
Used a targeted source snapshot and GitHub Git APIs, as permitted by AGENTS.md.
Full `bash scripts/pre_push.sh`: NOT_RUN; no full shell checkout is available.
Canonical `bash scripts/format.sh write` completed Ruff formatting, then exited 2
because the pinned Prettier is not installed. This is a partial formatter result,
not a successful full local gate. Exact-head CI is reported separately in the PR
and collection after publication; no earlier green status is reused.

Only these artifacts are published: original catalog/validator/tests, expanded
catalog/validator/tests, two catalog docs and this handoff. Downloaded runtime
sources and formatter helpers are not changes in this PR. D0 and
LOGOS_GENERATION_ALLOWED remain unchanged.

PR stays Draft while required CI/review gates remain incomplete. No manual Copilot
retrigger and no merge permission. A future runtime integration must resolve
model availability and per-record blockers, implement/review the adapter, and
obtain the applicable live-run scope/budget before sending provider requests.

# #1413 — Ultra model profile artifacts

Owner request: 24 October 2026, Ultra profiles for every registered model.
Canonical admission: https://github.com/maksimp6/Chat/issues/1413#issuecomment-6381606230
Base develop: `081d07af8f854c81201c9341c46c7ed5d3ee9ecf`.
Branch: `feat/1413-ultra-model-profiles`; primary owner root / Backend Engineer.

## Scope and accepted contract

Prepare maximum-capability plans for every ALL_MODELS key, with exact aliases,
documented model/transport settings and explicit unknowns. Preserve Pro5.1 async
candidate32768 and current production behavior. Scope is artifacts + offline
checks, not generation, profile activation, access changes, merge or deployment.
No approved provider runs or new paid services; no credential access.
Two independent Docs Engineer source checks covered text and Realtime separately.

## Result and tests

- All14 registry keys covered, 3 retired IDs blocked with no replacement request.
- GPT OSS high, Realtime inf selector/3600-second session boundary, model context
  limits and async32768 candidate are explicit. Unknown hosted output ceilings
  and reasoning/tool capabilities remain blockers, not invented maxima.
- Focused unittest: 18 tests PASS; validator CLI PASS; compileall PASS.
  Negative controls cover missing/new models, URI substitution, unknown IDs,
  retirement, context/output confusion, unsupported reasoning, API-field leakage,
  async downgrade, truncation, duplicate JSON and automatic paid retries.
- Runtime admission, actual model responses/tool E2E, provider acceptance and
  billed costs: NOT_RUN/UNKNOWN. New catalog is not loaded by production code.

## Local environment boundary

Authenticated shell git proxy is unavailable: git ls-remote returned
'could not read Username ... No such device or address'. Used a targeted source
snapshot plus GitHub Git APIs. Full scripts/pre_push.sh was NOT_RUN.
The canonical bash scripts/format.sh write ran Ruff0.17.0 successfully on the
snapshot, then exited2 because Prettier is not installed. This is a partial
formatter result, not a clean full formatting gate. Exact-head CI remains pending.
Only the five new catalog/validator/test/doc/handoff files are published; downloaded
baseline source and formatting helpers are not part of the change.

## Review and next owner

PR remains Draft while exact-head CI and automatic review are pending. Address
findings on this same branch; no manual Copilot retrigger. No merge permission.
Future Backend Engineer must resolve each capability/limit from provider evidence,
implement/review an adapter and receive run scope/budget before any live request.
D0 and LOGOS_GENERATION_ALLOWED remain unchanged.


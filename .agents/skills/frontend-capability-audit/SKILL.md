---
name: frontend-capability-audit
description: Map real UI interactions to current backend and CLI capabilities and distinguish missing UI from unfinished backend or deployment.
---

## Purpose
Produce a prioritized, source-bound frontend gap list for an existing product.

## Non-goals
No implementation, new Issues, paid/device actions, inferred production availability or replacement frontend simply because a scaffold exists.

## Inputs
Current base/head, repository instructions and docs, actual templates/components/routes, API/CLI/tool registrations, relevant PR metadata and user scenarios.

## Tools
Repository searches excluding generated/vendor bundles, branch diffs, read-only GitHub metadata, existing deterministic tests and an isolated local real browser where available.

## Procedure
1. Identify the actual served frontend and separate unused scaffolds, service-local pages and native-device UI.
2. Compare master and develop trees; inspect relevant PR code and actual base/draft/merge metadata.
3. Map scenario → visible control → request/tool → registered backend → persisted result → user-visible evidence.
4. Classify each gap: UI-only, missing API/security adapter, missing backend, draft-only implementation or unverified deployment. A code path alone does not prove production.
5. Run a disposable local UI with synthetic data and no production credentials. Block external calls; use controlled failures to exercise empty, unauthorized, timeout, unavailable and partial-result states.
6. Keep BrowserShim contract evidence separate from Chromium rendering/interaction evidence. Record exact failure steps and what was mocked.
7. Give each gap a priority, code source, missing interaction, acceptance criterion, dependency and proposed order.
8. Link existing canonical owners and docs; do not create new Issues or claim historical audit findings remain current without rechecking.

## Approval boundaries
Read-only/source/local-fixture work only. Device activation, paid sessions and production changes are separately scoped. Mocked success is not authorization or live acceptance.

## Validation
Each finding has a reproducible source or browser path and a falsifiable acceptance test. Existing UI is credited; uncertainty is retained for backend/deployment dependencies.

## Failure behavior
If the local environment cannot run safely, label the audit source-only. Do not equate an HTTP shell smoke with browser coverage, or one mobile viewport with accessibility acceptance.

## Output
Capability map, prioritized gap list, exact baselines, local/source/live evidence split and next implementation order.
Example: [2026-10-11 audit](../../../docs/frontend/capability-gaps-2026-10-11.md); [history scope](../../../docs/lessons/README.md).

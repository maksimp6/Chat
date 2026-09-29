---
name: docs-sync
description: Synchronize authoritative documentation with shipped repository behavior without presenting roadmap as implemented.
---
## Purpose
Keep documentation aligned with current code and operational reality.

## Non-goals
Do not change product behavior merely to match stale prose or turn plans into shipped claims.

## Inputs
- Relevant implementation and tests.
- Existing authoritative docs and roadmap issues.

## Tools
Use repository reads/search and documentation edits.

## Procedure
1. Identify the authoritative implementation and current shipped behavior.
2. Compare docs for contradictions, omissions and stale claims.
3. Separate shipped behavior from roadmap/future work.
4. Update the smallest authoritative documents and cross-links.
5. Avoid duplicating the same policy across many files.

## Approval boundaries
Documentation edits follow normal PR workflow; no production action is implied by prose.

## Validation
Every concrete claim is supported by current master-bound implementation or explicitly labeled as planned/unverified.

## Failure behavior
When implementation state is unclear, document the uncertainty instead of guessing.

## Output
Return changed docs, corrected claims and any unresolved implementation/documentation gaps.

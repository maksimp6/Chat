---
name: compliance-evidence-reconciliation
description: Compare implementation and privacy declarations using deterministic evidence while leaving legal decisions to human review.
---

## Purpose
Turn an engineering privacy/compliance consistency claim into inspectable evidence (H10).

## Non-goals
No government filing, legal-compliance declaration or assumption that named policy files prove controls.

## Inputs
Implementation/config revision, declared data map, dated normative references supplied for review, privacy documents and synthetic negative fixtures.

## Tools
Read-only code/config inspection and deterministic schema/reconciliation tests. Recheck current authoritative legal sources before any legal interpretation; this skill states no legal conclusion.

## Procedure
1. Define Inspector → machine-readable actual state → declared intent → reconciliation.
2. Track data categories, purpose, storage, processors, regions, retention and controls without collecting personal records into evidence.
3. Verify actual retention/delete/correct/block/access and backup behavior; absent evidence stays unknown.
4. Add negative fixtures for mismatched implementation/documentation and a synthetic incident drill.
5. Bind findings to code/config revisions and dates of normative sources.
6. Distinguish deterministic mismatch eligible for a proposed release gate from ambiguous legal interpretation requiring a qualified human decision.

## Approval boundaries
No new release gate, external declaration or legally significant submission is enabled by this documentation procedure.

## Validation
Known mismatches are detected, unknowns remain visible, a clean fixture passes and the report contains evidence rather than raw personal data.

## Failure behavior
Historical #732 is closed without merge; current #729 owns implementation. Do not claim the inspector or release gate is shipped merely because this skill exists.

## Output
Evidence map, deterministic discrepancies, unknown controls, normative freshness and human-review questions.
Sources: [H10](../../../docs/lessons/README.md), [#729](https://github.com/maksimp6/Chat/issues/729).

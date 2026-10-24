---
name: release-readiness
description: Verify release metadata, artifacts, exact-head CI, rollback and protected deployment boundaries.
---
## Purpose
Prove that a release candidate is technically ready without bypassing protected gates.

## Non-goals
Do not deploy production, approve on the owner's behalf, or reuse stale artifacts.

## Inputs
- Release/PR head SHA.
- Required checks, artifacts, version/changelog and rollback information.

## Tools
Use read-only GitHub release/workflow/artifact/repository inspection and repository validation commands.

## Procedure
1. Pin the candidate to an exact commit.
2. Verify required CI/checks and merge readiness for that exact head.
3. Verify version and changelog consistency.
4. Verify required artifacts correspond to the candidate head.
5. Verify rollback/recovery instructions are usable.
6. Identify any production approval still required.

## Approval boundaries
Release preparation is read-only; production deployment and protected merge remain separate approved actions.

## Validation
All evidence references the same exact candidate commit and no required gate is stale, pending or missing.

## Failure behavior
Fail closed on missing artifacts, mismatched SHAs, stale checks or absent rollback evidence.

## Output
Return candidate SHA, readiness blockers, artifacts and required human approvals.

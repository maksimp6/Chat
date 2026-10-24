---
name: github-pr-readiness
description: Check whether a GitHub pull request is ready to merge on its exact current head.
---
## Purpose
Prove merge readiness from current repository state.

## Non-goals
Do not merge, bypass protection, or reuse stale CI evidence.

## Inputs
- Repository and PR number.
- Protected base branch and required checks.

## Tools
Use read-only GitHub PR, compare, checks, reviews and review-thread operations.

## Procedure
1. Read the PR and exact current head SHA.
2. Compare the head with the protected base and require behind-by zero.
3. Read required checks for that exact head and require successful conclusions.
4. Require no unresolved review threads and no blocking review state.
5. Re-read head/base state immediately before declaring readiness.
6. Return blockers instead of guessing when evidence is incomplete.

## Approval boundaries
The skill is read-only. Merge remains a separate protected action.

## Validation
Ready means exact-head checks are green, the branch is current, the PR is open/non-draft, and review blockers are absent.

## Failure behavior
Fail closed on missing, stale, pending or contradictory evidence.

## Output
Return ready/not-ready plus the exact head SHA and concrete blockers.

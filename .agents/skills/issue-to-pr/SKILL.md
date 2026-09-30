---
name: issue-to-pr
description: Convert one ready GitHub issue into a focused branch and pull request with deterministic validation.
---
## Purpose
Deliver one minimal vertical slice from issue to reviewable PR.

## Non-goals
Do not mix unrelated refactors, deploy production, or write directly to master.

## Inputs
- Ready issue with goal, acceptance criteria and scope.
- Current protected master.

## Tools
Use repository reads, branch/file writes, tests and pull-request creation allowed by the active role.

## Procedure
1. Inspect the issue and current master.
2. Identify the smallest independent implementation slice.
3. Create a focused branch.
4. Implement only the required change.
5. Add deterministic regression coverage.
6. Run focused checks, then repository-required validation.
7. Open a PR linked to the issue and keep it draft while implementation/CI changes.
8. Publish exact failures instead of claiming completion when publication is blocked.

## Approval boundaries
Never bypass protected master, production approval, secret policy or destructive-data confirmation.

## Validation
The PR matches the issue, contains regression evidence and passes required checks on its current head.

## Failure behavior
Stop and report a concrete blocker when required access, credentials or evidence are unavailable.

## Output
Return branch/PR identity, changed scope, checks run and remaining blockers.

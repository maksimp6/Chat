---
name: cloudru-change
description: Plan and execute a Cloud.ru change through preflight, approval, verification and rollback evidence.
---
## Purpose
Make Cloud.ru infrastructure changes reproducible and policy-gated.

## Non-goals
Do not create billable production resources from assumptions or expose credentials.

## Inputs
- Desired Cloud.ru state.
- Current resource inventory/configuration.
- Cost, identity and approval context.

## Tools
Prefer read-only Cloud.ru/API/CLI inventory first; mutating cloud tools only after required approval.

## Procedure
1. Read current state and identify the exact resource delta.
2. Validate region/service availability, identity and least-privilege permissions.
3. Estimate relevant cost/risk from actual configuration.
4. Produce a deterministic change plan and rollback path.
5. Obtain explicit approval where policy requires it.
6. Apply the smallest change.
7. Verify health/state and capture identifiers/evidence.
8. Roll back or fail closed when verification fails.

## Approval boundaries
Production deploys, billable resource creation, secret/certificate changes and destructive operations require explicit approval.

## Validation
The resulting cloud state matches the approved plan and health checks pass.

## Failure behavior
Do not claim deployment or creation until externally verified; preserve rollback information.

## Output
Return before/after state, actions, verification, cost/risk notes and rollback status.

---
name: security-review
description: Review repository changes for secrets, permissions, trust-boundary and runtime-isolation regressions.
---
## Purpose
Produce actionable security findings grounded in concrete code or configuration.

## Non-goals
Do not invent vulnerabilities, rotate secrets, or make unrelated implementation changes.

## Inputs
- Change diff or targeted files.
- Existing repository security/approval policy.

## Tools
Use read-only code, workflow, dependency and configuration inspection plus safe validators.

## Procedure
1. Inspect changed trust boundaries and permission scopes.
2. Check secret handling, logs, traces and error surfaces.
3. Check tool/approval/authentication boundaries.
4. Check workflow/event trust and third-party action pinning.
5. Check runtime/resource isolation for affected code.
6. Report only findings with a concrete failure mode and location.

## Approval boundaries
The review is read-only. Remediation that changes secrets, permissions or protected settings follows normal approval policy.

## Validation
Each finding is reproducible or directly evidenced and includes severity rationale without sensational labels.

## Failure behavior
Mark uncertain hypotheses as unverified and do not promote them to findings.

## Output
Return actionable findings with evidence, impact and minimal remediation.

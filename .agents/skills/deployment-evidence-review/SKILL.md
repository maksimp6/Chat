---
name: deployment-evidence-review
description: Review whether desired configuration, protected source, tested artifact and observed deployment describe the same system.
---

## Purpose
Review provenance and operational evidence across config, build and deployment (H09/H11). Complements release-readiness and cloudru-change.

## Non-goals
No resource mutation, secret resolution into reports, ruleset changes or invented deployment success.

## Inputs
Exact source head, current canonical config/schema, admitted checks, immutable image digest, lane/resource identity and sanitized observation/health evidence.

## Tools
Read repository config, planner/validators and existing provider evidence. Use dry-run fixtures that count mutation calls.

## Procedure
1. Keep three layers aligned: docs explain policy; config/platform stores desired facts; logos_platform observes/plans/reconciles.
2. Validate unknown fields, duplicate resources, dependency cycles, secret-value exclusion and exact admitted origins.
3. Inventory service-specific authentication and lifecycle: IAM, deploy, gateway, model, storage and database credentials are not interchangeable. Distinguish app/build/audit/gateway/model logs.
4. Confirm read-only observation is provider-backed; unknown observations stay UNKNOWN/BLOCKED. Assert dry-run performs zero mutations.
5. Check test/production privilege separation and least-required inputs; test lanes must not inherit production authority.
6. Bind protected source → required exact-head checks → built immutable digest → deployed resource/config. Promotion reuses the admitted artifact.
7. Treat older revision selection as explicit rollback, not normal deployment. Rollback evidence contains approved config/secret references, not plaintext historical provider records.
8. Require post-deploy health, auth/tool checks and controlled restart/restore evidence before an operational readiness claim.

## Approval boundaries
This procedure reviews evidence. Deployment, stop, adoption, rollback, DNS, secrets and permission changes retain separate owner approval.

## Validation
Source/artifact/resource identity matches; required checks and lane separation hold; missing live evidence is clearly distinguished from validated local config.

## Failure behavior
A merged runbook is not a rollout. A branch name is not immutable source. An ancestor commit is not current release admission. Placeholder success and an invented empty observed state fail the review.

## Output
Provenance chain, safe drift/readiness findings, UNKNOWN/BLOCKED observations and the next authorized verification.
Sources: [H09/H11](../../../docs/lessons/README.md), [#783](https://github.com/maksimp6/Chat/issues/783).

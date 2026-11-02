---
name: change-delivery
description: Take a change from a ready issue to a verified release. Use to implement one issue and push it to develop, to check that a commit or release candidate is ready (exact-commit checks, artifacts, rollback), to check an existing pull request's readiness, or to review a Cloud.ru infrastructure change.
---
## Purpose
Deliver one minimal change and show that it is ready to release, without bypassing protected gates. Three modes share one rule: every piece of evidence refers to the same exact commit.
- **Implement**: one focused slice from a ready issue, pushed directly to `develop`.
- **Release**: exact-commit readiness, artifact and deployment provenance, rollback, and Cloud.ru changes.
- **Existing PR**: read-only readiness of a pull request that already exists, on its exact head.

## Non-goals
- Do not create branches or pull requests for code changes. Push directly to `develop`.
- Do not write or run tests, typechecks or builds unless the owner asks.
- Do not mix unrelated refactors into a change.
- Do not write to `master`, deploy to production, merge a protected PR, or approve on the owner's behalf.
- Do not reuse stale checks or artifacts, and do not rebuild an admitted artifact for promotion.
- Do not mutate cloud resources during a review, and do not resolve secrets into reports.

## Inputs
- A ready issue with its goal, acceptance criteria and scope, and the current `develop` HEAD.
- For release: the candidate commit SHA, required checks for that commit, version and changelog, the immutable artifact digest, and rollback information.
- For Cloud.ru: the desired state, current inventory and configuration, and cost and identity context.
- For an existing PR: the PR number, its exact head SHA, and the protected base branch.

## Tools
- Repository reads, file writes and direct pushes to `develop` allowed by the active role.
- Read-only GitHub release, workflow, artifact, PR, compare and review-thread reads.
- Repository config, planner and validator files, and dry-run fixtures that count mutation calls.
- Read-only Cloud.ru inventory. Mutating tools only after the required approval.
- Tests, checks and builds only when the owner asks.

## Procedure
**Implement (one issue)**
1. Read the issue and the current `develop` HEAD. Identify the smallest independent slice.
2. Make the focused change directly on `develop`. Do not create a feature branch.
3. Implement only what the issue requires.
4. Add regression coverage only when the owner asks. Otherwise name the missing coverage.
5. Review the exact diff by reading it. Run checks only when the owner asks, otherwise report `NOT_RUN`.
6. Push the commit directly to `develop`.
7. If publication is blocked, report the exact failure. Never route around it through a PR.

**Release (candidate commit)**
1. Pin the candidate to an exact commit. A branch name is not immutable source, and an ancestor commit is not current release admission.
2. Verify the chain: protected source → required checks on that exact commit → built immutable digest → deployed resource and config. Promotion reuses the admitted artifact.
3. Check that the version and changelog agree, and that required artifacts correspond to the candidate commit.
4. Check the config: docs state the policy, config states the desired facts, and the platform observes, plans and reconciles. Validate unknown fields, duplicate resources, dependency cycles, secret-value exclusion and exact admitted origins.
5. Check credentials and lanes. IAM, deploy, gateway, model, storage and database credentials are not interchangeable, and test lanes must not inherit production authority. A dry run must perform zero mutations.
6. Read observations honestly. Unknown observations stay `UNKNOWN` or `BLOCKED`, and an empty observed state is never invented.
7. Check rollback. Returning to an older revision is an explicit rollback, not a normal deployment, and its evidence references approved config and secrets, never plaintext records.
8. Require post-deploy health, auth and tool checks before any operational readiness claim.
9. Name the approvals still required.

**Cloud.ru change**
1. Read the current state and name the exact resource delta.
2. Validate region, service availability, identity and least privilege. Estimate cost and risk from the actual configuration.
3. Write a deterministic plan and rollback path, then get the required approval.
4. Apply only the smallest approved change. Verify health and state, record identifiers, and roll back or fail closed if verification fails.

**Existing PR (read-only)**
1. Read the PR and its exact head SHA.
2. Compare the head with the protected base and require it to be current.
3. Read the required checks for that exact head, and require successful conclusions.
4. Require no unresolved review threads and no blocking review state.
5. Re-read the head and base state immediately before declaring readiness. Return blockers instead of guessing.

## Approval boundaries
Implementation pushes to `develop` follow the repository's publication rule. Release preparation and PR readiness are read-only. Production deployment, rollback, protected merges, DNS, secrets, permission changes, billable resource creation and destructive operations each need the owner's separate, explicit approval. A runbook being merged is not a rollout.

## Validation
- Implement: the pushed change matches the issue, the diff review is recorded, and test results are `NOT_RUN` unless the owner requested them.
- Release: every piece of evidence references the same exact commit, required checks on that commit are green and current, lane separation holds, and missing live evidence is distinguished from validated local configuration.
- Cloud.ru: the resulting state matches the approved plan and its health checks pass.
- Existing PR: ready means the exact-head checks are green, the branch is current, the PR is open and not a draft, and no review blocker exists.

## Failure behavior
Fail closed on missing artifacts, mismatched SHAs, stale or pending checks, absent rollback evidence or a placeholder success. Do not describe a plan as a deployment, a build as a release, or a branch as immutable source.

## Output
- Implement: the `develop` commit SHA, the changed scope, the checks run (or `NOT_RUN`) and the remaining blockers.
- Release: the candidate SHA, the provenance chain with its gaps, readiness blockers, artifacts, `UNKNOWN` or `BLOCKED` observations, rollback status, cost and risk notes, and the next required human approval.
- Existing PR: ready or not ready, the exact head SHA, and the concrete blockers.
Sources: [docs/lessons/README.md](../../../docs/lessons/README.md), [docs/cloudru-container-apps.md](../../../docs/cloudru-container-apps.md).

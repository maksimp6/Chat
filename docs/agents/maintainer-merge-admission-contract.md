# Maintainer protected merge admission contract

Issue: #712. Parent: #663. Contract stage: Test Engineer. Base: current protected `master`.

## Scope

This RED contract defines a small, pure admission decision for a proposed
Maintainer protected merge. It does not grant a token, enable a workflow,
mutate GitHub, or replace `scripts/merge_readiness.py`. The implementation
must reuse that existing fail-closed readiness evaluation and collect a fresh
snapshot immediately before a protected merge operation.

The proposed public boundary is
`scripts.maintainer_merge.admit_merge_request(request, snapshot, *,
actor_authorized, capability_available, consumed_requests)`.
It returns a dict with `ready`, `head_sha`, and `blockers` (each with a
stable `code`). The request names the repository, PR number, exact expected
head SHA, unique request key, and explicit `Maintainer/protected_merge` intent.

## Admission rules

- Reject missing or documentary intent, missing request identity, unauthorized
  actor, unavailable *actual* merge capability, or a consumed request key.
- Require the request's repository, PR number, and expected head to match the
  freshly collected snapshot. A changed head/base, draft, behind-master head,
  pending or failed required check, or unresolved/truncated review threads is
  rejected through the existing readiness evaluator.
- Return the exact head only after admission succeeds. Admission does not mark
  the request consumed; the trusted executor must claim it atomically before
  attempting a mutation and persist the terminal outcome.
- A trusted executor must recollect readiness at execution time and use the
  GitHub protected merge endpoint with the expected head SHA. Branch
  protection remains authoritative if the base moves between checks.
- Model prompts and comments carry no GitHub token. An owner must approve any
  privileged workflow or agent-authority expansion before it is enabled.

## RED evidence

`tests/test_maintainer_merge_contract.py` must fail to import
`scripts.maintainer_merge` on the contract branch, while existing repository
tests remain unchanged. Independent contract review accepts the exact test
head before an Infra Engineer implements the smallest production slice on a
stacked branch. Do not relax accepted assertions to make implementation green.

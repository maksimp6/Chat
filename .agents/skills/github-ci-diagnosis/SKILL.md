---
name: github-ci-diagnosis
description: Diagnose failing GitHub Actions jobs and reduce them to a minimal actionable root cause.
---
## Purpose
Turn red CI into a focused, reproducible engineering diagnosis.

## Non-goals
Do not rerun blindly, rewrite unrelated code, or weaken required checks.

## Inputs
- Repository, workflow run or PR head.
- Failed job/check names.

## Tools
Use GitHub workflow/job/step/log reads and repository file reads. Mutation requires normal repository workflow.

## Procedure
1. Identify failures on the exact current head.
2. Read the smallest relevant job steps and logs.
3. Separate infrastructure/transient failures from deterministic code/test failures.
4. Map the first causal failure to touched files or configuration.
5. Reproduce with the narrowest repository command when possible.
6. Propose the smallest fix and regression coverage.

## Approval boundaries
Reruns and code changes follow repository mutation policy; never bypass checks.

## Validation
A diagnosis names the failing check, causal evidence, reproduction path and success criterion.

## Failure behavior
If logs are missing or ambiguous, report the evidence gap rather than inventing a cause.

## Output
Return root cause, evidence, focused reproduction and next corrective action.

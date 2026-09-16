# Auditable Rollback Workflow

Every rollback must be handled as a normal, reviewable change rather than a direct edit to `master`.

## Required sequence

1. Create a dedicated GitHub Issue describing:
   - the reason for the rollback;
   - the original PR, commit, or release being reverted;
   - the affected behavior or scope;
   - the validation and recovery plan.
2. Create a new branch from the current `master`, named `issue-<number>-rollback`.
3. Implement the rollback in that branch and commit the change.
4. Open a dedicated Pull Request that references the rollback Issue.
5. Run CI and complete review before merging.
6. After merging, verify the affected behavior and record the outcome in the Issue or PR.

## Traceability requirements

The Issue, branch, rollback commit, and PR must link to one another. The PR description should identify the original change being reverted and include the checks performed.

## Emergency exception

A direct emergency rollback may be used only when delaying the rollback creates an immediate operational or security risk. The emergency action must be documented afterward with the same information required by the normal workflow.

## Example PR body

```markdown
Closes #<rollback-issue>

## Rollback target
- Original PR/commit: <link>
- Reason: <reason>
- Scope: <affected area>

## Validation
- <check 1>
- <check 2>

## Post-merge verification
- <result or follow-up task>
```

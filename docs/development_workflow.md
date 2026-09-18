# Reproducible Development Workflow

This document defines the default workflow for changes to Chat/Alice Pro.

## 1. Start with an issue

- Describe the problem, expected behavior, scope, and acceptance criteria.
- Split large work into independently testable steps.
- Link related issues and document assumptions.

## 2. Create an isolated branch

Use a branch named after the issue:

```text
issue-<number>-<short-description>
```

Never commit directly to `master`. Repository rules require changes to go through a pull request.

## 3. Implement the smallest safe change

- Keep unrelated refactoring out of the change.
- Preserve existing public behavior unless the issue explicitly changes it.
- Do not place secrets in source code, logs, traces, fixtures, or API responses.
- Make failures explicit rather than reporting success after a failed operation.

## 4. Add regression coverage

Every bug fix should include a focused regression test where practical. Tests should cover:

- successful execution;
- expected failure paths;
- malformed or missing data;
- timeout and retry behavior where relevant;
- serialization and trace safety for execution-related changes.

## 5. Run local checks

Run the checks supported by the project before opening a PR. At minimum, execute the relevant test suite and build or syntax checks for modified components.

Record failures honestly in the PR description. A red test is information, not a personal insult from the universe.

## 6. Open a pull request

The PR should include:

- a concise summary of the change;
- the linked issue, using `Closes #<number>` when appropriate;
- test commands and their results;
- known limitations and follow-up work;
- migration or rollback notes for data and configuration changes.

Prefer draft PRs while the implementation is incomplete.

## 7. Review and CI

- Inspect the complete diff for unrelated changes and secret exposure.
- Wait for required CI checks.
- Investigate failures using the workflow logs instead of rerunning blindly.
- Update the branch and re-check CI after fixes.

## 8. Merge and verify

After approval and green required checks:

- merge the PR using the repository's configured strategy;
- verify that the issue was closed when intended;
- confirm the resulting change exists on `master`;
- perform a focused smoke test when the change affects runtime behavior.

## 9. Rollback

For a regression:

1. identify the merge commit and affected behavior;
2. disable the affected feature or revert the PR when necessary;
3. preserve logs and trace identifiers without exposing secrets;
4. create a follow-up issue describing the failure and prevention test.

## Definition of done

A change is complete only when its implementation, tests, documentation, review, CI checks, and post-merge verification are accounted for.

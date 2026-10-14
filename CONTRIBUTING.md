# Contributing to Alice Pro

All production changes follow the same reviewable flow:

**Issue → branch → implementation → tests → PR → CI → merge**

Keep PRs small enough to validate independently. Every behavior change should include regression coverage when practical.

## Pull requests

Describe the user-visible behavior, technical change, risks, validation, and related issue. Do not paste credentials, provider keys, access tokens, or private URLs into GitHub.

The `master` branch is the protected production branch. Routine development PRs must target `develop`, not `master`. Agents push only to their own task branches and must verify the PR's actual base is `develop`. Promotion from `develop` to `master` requires a separate explicitly authorized integration PR and protected CI/review gates. Stage acceptance (`OK`) does not authorize merge. Data-model changes follow the current native MemoryStore contract and must pass the applicable tests before deployment.

## Local web assets

Required UI resources such as JavaScript, CSS, icons, and debugging UI bundles are repository-local. External network access belongs to API/service calls, not static application assets.

## Tracing

Tool execution, provider requests, polling, and agent calls should preserve `trace_id`/invocation correlation and must not expose secrets in ExecutionTrace.

C sources and native bridges follow [C17 development standard](docs/development/c-standard.md); run `bash scripts/check_c17.sh` when modifying C.

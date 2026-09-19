# Contributing to Alice Pro

All production changes follow the same reviewable flow:

**Issue → branch → implementation → tests → PR → CI → merge**

Keep PRs small enough to validate independently. Every behavior change should include regression coverage when practical.

## Pull requests

Describe the user-visible behavior, technical change, risks, validation, and related issue. Do not paste credentials, provider keys, access tokens, or private URLs into GitHub.

The `master` branch is the production branch for the current repository workflow. Database migrations are applied by the Supabase production workflow after changes reach `master`.

## Local web assets

Required UI resources such as JavaScript, CSS, icons, and debugging UI bundles are repository-local. External network access belongs to API/service calls, not static application assets.

## Tracing

Tool execution, provider requests, polling, and agent calls should preserve `trace_id`/invocation correlation and must not expose secrets in ExecutionTrace.

# Contributing to Logos

`develop` is the default integration branch; `master` is the owner's personal branch. Follow the delivery route explicitly requested by the owner. Promotion to `master` requires separate authorization. Stage acceptance (`OK`) does not authorize merge.

## Changes and verification

Describe the user-visible behavior, technical change, risks and validation actually performed. Project CI, lint, typing, naming and layout gates have been removed. Only real Redmi 9 checks remain; run them inside GitHub Actions only when explicitly requested. See [Redmi 9 checks](docs/architecture/browser-self-testing.md). Operational deployment checks still track process identity, application readiness, data preservation and rollback.

Do not paste credentials, provider keys, access tokens or private URLs into GitHub. Data-model changes follow the current native Kristall contract.

## Local web assets

Required UI resources such as JavaScript, CSS, icons and debugging UI bundles are repository-local. External network access belongs to API/service calls, not static application assets.

## Tracing

Tool execution, provider requests, polling and agent calls should preserve `trace_id`/invocation correlation and must not expose secrets in ExecutionTrace.

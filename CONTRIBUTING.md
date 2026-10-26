# Contributing to Logos

Start with the owner's current instruction and [AGENTS.md](AGENTS.md).
They define delivery and test execution; this guide does not add another default
publication route.

`develop` is the integration branch; `master` is the owner's personal branch.
Promotion to `master` requires separate authorization. Stage acceptance (`OK`)
does not authorize merge.

## Change and evidence

Describe the user-visible result, change, risks and verification actually performed.
Read the final diff and reconcile the current source head before publication.

Code tests run only on the owner's separate request, inside a GitHub Actions
runner. Without a run, report `NOT_RUN`; never imitate a runner environment.
Removed CI, lint, typing, naming and layout gates are not required checks.

Deployment evidence records process identity, application readiness, data
preservation and the actual outcome. It does not replace a user-visible check.
See the [development workflow](docs/development_workflow.md).

Do not publish credentials, provider keys, access tokens or private URLs.
Data changes follow the [native Kristall contract](docs/database.md).

## Local web assets and tracing

Required JavaScript, CSS and icons are repository-local. External network access
belongs to API/service calls rather than static UI dependencies.

Tool execution, provider requests, polling and agent calls preserve
`trace_id`/invocation correlation and keep secrets out of ExecutionTrace.

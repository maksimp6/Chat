# Contributing to Logos

Thanks for helping improve Logos. This guide covers how changes are described,
what counts as evidence and the project's code style.

> [!IMPORTANT]
> Start with the owner's current instruction and [AGENTS.md](AGENTS.md).
> They define delivery and test execution; this guide does not add another
> default publication route.

**Contents:**
[Order of precedence](#order-of-precedence) ·
[Branches](#branches) ·
[Describing a change](#describing-a-change) ·
[Verification and evidence](#verification-and-evidence) ·
[Credentials and data](#credentials-and-data) ·
[Local web assets and tracing](#local-web-assets-and-tracing) ·
[Code style](#code-style) ·
[Checklist](#checklist) ·
[Getting help](#getting-help)

## Order of precedence

1. The owner's current instruction.
2. [AGENTS.md](AGENTS.md): delivery route, test execution and settings policy.
3. The [development workflow](docs/development_workflow.md) and this guide.

## Branches

| Branch | Role |
| --- | --- |
| `develop` | The integration branch. |
| `master` | The owner's personal branch. Promotion to `master` requires separate authorization. |

Stage acceptance (`OK`) does not authorize merge.

## Describing a change

- Describe the user-visible result, the change, its risks and the verification
  actually performed.
- Read the final diff and reconcile the current source head before publication.

## Verification and evidence

- Only real [Redmi 9 checks](docs/architecture/browser-self-testing.md) remain.
- Code tests run only on the owner's separate request, inside a GitHub Actions
  runner. Writing or changing code tests also requires an explicit owner request
  (see [AGENTS.md](AGENTS.md)).
- Without a run, report `NOT_RUN`. Never imitate a runner environment.
- Removed CI, lint, typing, naming and layout gates are not required checks.
- Deployment evidence records process identity, application readiness, data
  preservation and the actual outcome. It does not replace a user-visible check.

See the [development workflow](docs/development_workflow.md) for completion
criteria and reporting.

## Credentials and data

- Do not publish credentials, provider keys, access tokens or private URLs.
- Data changes follow the [native Kristall contract](docs/database.md).
- Report vulnerabilities privately as described in [SECURITY.md](SECURITY.md).

## Local web assets and tracing

- Required JavaScript, CSS and icons are repository-local. External network
  access belongs to API/service calls rather than static UI dependencies. Pinned
  upstream browser libraries live in [`static/vendor/`](static/vendor/README.md).
- Tool execution, provider requests, polling and agent calls preserve
  `trace_id`/invocation correlation and keep secrets out of ExecutionTrace. See
  [Execution Trace and system log](docs/backend/logging.md).

## Code style

**Python** can be formatted with [Ruff](https://docs.astral.sh/ruff/). The pinned
version is in [`requirements-dev.txt`](requirements-dev.txt); settings live in
`[tool.ruff]` in [`pyproject.toml`](pyproject.toml).

```bash
python -m pip install -r requirements-dev.txt
bash scripts/format.sh write   # apply formatting
bash scripts/format.sh check   # report files that would change, without changing them
```

Both modes run over the whole checkout except the paths excluded in
`pyproject.toml`. They are optional (see the [scripts guide](scripts/README.md)):
neither is a required check, and a formatting result is not test evidence.

**Editor settings** come from [`.editorconfig`](.editorconfig): UTF-8, LF line
endings, a final newline, no trailing whitespace (except in Markdown) and the
indentation each file type already uses. Vendored upstream code keeps its
original formatting.

**Naming and reliability** recommendations are in
[naming conventions](docs/development/naming.md) and
[reliability rules](docs/development/reliability.md). The test files those pages
mention have been removed; the pages are guidance, not gates.

## Checklist

A self-review aid, not a required check:

- [ ] The owner's current instruction and [AGENTS.md](AGENTS.md) are followed for delivery and tests.
- [ ] The description states the user-visible result, change, risks and verification actually performed.
- [ ] The final diff is read and the current source head is reconciled before publication.
- [ ] Checks that did not run are reported as `NOT_RUN`; no runner environment is imitated.
- [ ] No credentials, provider keys, access tokens or private URLs are published.
- [ ] Data changes follow the native Kristall contract.
- [ ] Required JavaScript, CSS and icons stay repository-local.
- [ ] `trace_id`/invocation correlation is preserved and secrets stay out of ExecutionTrace.

## Getting help

Questions go to [SUPPORT.md](SUPPORT.md). Participation follows the
[Code of Conduct](CODE_OF_CONDUCT.md).

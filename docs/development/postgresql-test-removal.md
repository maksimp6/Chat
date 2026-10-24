# Remove PostgreSQL from Alice test infrastructure

Status: implementation checklist, not a completed removal.

## Scope

Remove PostgreSQL execution from all automated tests and CI, not only the visible job. Preserve the required CI fail-closed semantics. Do not delete production SQL runtime until separately migrated and verified.

## Inventory from repository scan

- `.github/workflows/ci.yml`: `postgres` job, service, requirements and backup/restore commands, `required.needs`.
- `.github/workflows/merge-readiness.yml`: legacy PostgreSQL check name.
- `tests/conftest.py`, `tests/postgres_test_guard.py`: PostgreSQL reset fixture and guard.
- `tests/test_ci_efficiency_contract.py`, `tests/test_ci_platform_changes.py`, `tests/test_ci_develop_workflows.py`, `tests/test_ci_runner_cache_contract.py`: CI tests assuming PostgreSQL.
- `scripts/ci_platform_changes.py`: classify/verify PostgreSQL as a platform.
- Application tests such as `tests/test_budget_repository.py`, `tests/test_async_runtime_concurrency.py`, `tests/test_cloudru_container_deploy.py` still reference PostgreSQL and must be reviewed before removal; preserve meaningful negative/security coverage or replace with file-native equivalents.
- `.github/workflows/cloudru-deploy.yml` installs `requirements-postgres.txt`; this is deployment, not a test-only change, and needs separate runtime compatibility review.

## Acceptance gates

1. No PostgreSQL service, job, client package or database URL used by test workflows.
2. No PostgreSQL reset fixture or destructive database test path.
3. CI platform classifier, `required.needs`, merge readiness and their tests updated atomically.
4. FileMemoryDB tests cover commit, recovery, replay, integrity, backup/restore and access boundaries.
5. Exact-head CI success, independent tests, and explicit review of remaining PostgreSQL references.
6. Distinguish intentionally skipped jobs from failures. Never hide a required check by deleting it without updating the policy and tests.

No merge.

# SQL dependency inventory and retirement gates (#776)

**Snapshot audited:** protected `master`, 2026-10-12; inventory root is the complete Git tree, not a hand-picked list of SQL files. No SQL code has been removed, no state migrated, no cutover authorized.

## Purpose and verification boundary

The accepted #776 / #780 architecture targets the durable file-native Memory DB. This is a **migration audit**, not a proposal to retain SQLite or PostgreSQL as the target. Existing PostgreSQL CI remains meaningful until legacy consumers are migrated or retired.

`scripts/sql_inventory.py` scans **every tracked file** from the exact `master` Git ref and emits `master.json` + `master.md`; a second scan inventories the PR HEAD. Both outputs are attached to each run of **SQL dependency inventory**. The JSON includes exact paths, categories, migration wave, marker rule IDs, and line numbers. No raw source lines, credentials, screenshot or PII are written into artifacts.

- GitHub tree preflight, 2026-10-12: **939 Git blobs** across **1,055 tree entries**, tree not truncated. Exact `master` at `51aaf8273f4f465fe78aa0a79e76a22d8f141e35`: **939 tracked files, 937 scanned UTF-8 text files, 160 marker files, 32 direct runtime SQL-marker files**. Two JavaScript/frontend matches were excluded from authoritative runtime debt after independent review. The remaining **3 unassigned direct runtime files** are `agent_shell/__main__.py`, `agent_shell/store.py` and `tool_providers/filesystem.py`. Counts are from [exact-head SQL inventory workflow #38218729975](https://github.com/maksimp6/Chat/actions/runs/38218729975), not from search-index estimates.
- The scanner tracks unreadable/binary files explicitly and does not infer that indirect SQL usage is absent merely because a file has no keyword match.
- Evidence is **lexical/static**, not runtime-reachability or data equivalence. Unknown runtime owners are listed as `triage`, never silently assigned to a wave.
- The guard compares tracked PR HEAD to current `origin/master` and blocks new **direct runtime SQL** signal occurrences per path/rule. Existing debt may decrease; the test remains green while waves are in progress. Frontend, docs, tests, CI and infrastructure references remain visible but do not count as new production SQL consumers.
- For exact file-specific evidence, refer to the `master.md`/`master.json` artifacts, not the representative list below.

## Machine inventory classification (pinned master)

| Area | Marker files |
|---|---:|
| Runtime | 33 (32 direct, 1 reference-only) |
| Tests | 50 |
| CI | 6 |
| Documentation | 46 |
| Frontend (non-authoritative UI) | 4 |
| Infrastructure / deploy | 10 |
| SQL migrations | 5 |
| Other tracked configuration | 6 |

Runtime waves: **wave 1: 6**, **wave 2: 9**, **wave 3: 12**, **wave 4: 2**, plus **4 runtime triage-marker files** (3 direct, 1 reference-only). The two non-text tracked files are `.coverage` and `r/frontend/src/assets/hero.png`; both are explicitly listed as skipped in the generated JSON.

## Confirmed runtime migration inventory, representative direct dependencies

| Wave | Confirmed named consumers / boundary | Assessment |
|---|---|---|
| 1, user / chat / memory | `session_manager.py`, `memory_manager.py`, `memory_extractor.py` | `get_conn` remains; verify data authority and move to durable typed APIs |
| 1, already changed | `user_identity.py`, `conversation_ownership.py`; `agent_memory/user_identity_migration.py`, `agent_memory/conversation_ownership_migration.py` | First two have no direct SQL markers in the inspected current files; migration import readers still reference SQL. Absence of a marker does **not** prove all indirect dependencies are gone |
| 2, control / execution | `invocation/manager.py`, `runtime_migrations.py`, `observability_migrations.py`, `alice_agent_runner.py`, `environment_manager.py`, `mcp_storage.py`, `local_agent_gateway.py`, `agent_memory/repository.py` | SQL session/event/queue consumers; no partial authority during cutover |
| 3, finance / business / providers | `treasury.py`, `budget_repository.py`, `departments.py`, `government.py`, `partner_relations.py`, `printing3d/service.py`, `printing3d/finance.py`, `provider_quotas.py`, `key_manager.py`, `provider_credentials_routes.py` | Replace business SQL repositories; keep secrets within #755 boundary |
| 4, adapters / compatibility | `db.py`, `db_backend.py`, `memory_db.py` | `db.py` still selects SQLite/PostgreSQL; `db_backend.py` has psycopg translation; process-local `memory_db.py` is not the durable replacement |
| Unassigned / manual triage | `agent_shell/store.py`, `agent_shell/__main__.py`, `tool_providers/filesystem.py`, `archiver.py`, provider/client and helper modules flagged by scanner | Confirm owner, reachability and accepted migration wave before changing behavior |

## Tests, CI, migrations, deploy and documentation

| Area | Legacy sources requiring eventual update | Condition to retire |
|---|---|---|
| CI | `.github/workflows/ci.yml` PostgreSQL integration job and `CI required` dependency; `scripts/ci_platform_changes.py` database selector and postgres result ratchet | Replacement Memory DB gates on same head are green and branch rules adjusted deliberately |
| Tests | `tests/test_postgres_integration.py`, `tests/postgres_test_guard.py`, `tests/test_postgres_reset_guard.py`, `tests/test_db_backend.py`, `tests/test_chat_sqlite_integration.py`, migration/SQL portability suites | Equivalent typed API and restore/recovery coverage; not merely deletion to turn CI green |
| Packages | `requirements-postgres.txt` (`psycopg[binary]`) | No supported runtime consumer or CI tool needs it |
| Deploy | `docker-compose.postgres.yml`, `deploy/cloudru/postgres/*`, `scripts/pg_backup.sh`, database env examples | File-native backup restore, deploy/restart, credential-boundary and recovery proof accepted |
| Migration | `migrations/*.sql`, `mcp_migration.sql`, SQL legacy migration scripts | Shadow-import/compare complete; bounded import window and rollback contract accepted |
| Docs | `README.md`, `docs/database.md`, `docs/production-deployment.md`, `docs/setup/installation.md`, Cloud.ru/Postgres and runtime/architecture notes | Document actual shipped capability, no premature target claims |

## File-native progress is real but does not imply completed cutover

- `agent_memory/file_memory_db.py` has append-only checksummed records and fsync, recovery, and compaction; its in-process `RLock` **is not** a multiprocess writer barrier.
- `agent_memory/backup.py` contains local backup/restore verification; acceptance still needs fault injection and isolated restore matching deterministic invariants.
- `memory_db.py` is a typed, **process-local** MemoryDatabase, distinct from the durable `FileMemoryDB`.
- SQL-backed consumer code and optional PostgreSQL deployment are still present in `master`; direct and indirect authority must be audited independently.

## Non-destructive retirement sequence

1. **Engine proof**: crash/torn-tail/corruption/ENOSPC under fault injection; interprocess exclusion; fsync on parent directory; compaction safety; isolated backup/restore and encryption/KMS context as applicable. Preserve evidence in #776 and #783.
2. **Wave 1**: owner, conversation, session, memory stores. Import while old source remains authoritative; shadow-verify counts, record hashes, owner assignments, restart and backup restore. Explicitly authorize any cutover later.
3. **Wave 2**: invocation, runtime, observability, MCP/device queues. Preserve replay/idempotency and Execution Trace semantics.
4. **Wave 3**: Treasury, budgets, partner, government, printing3d, provider/config. Verify business invariants and payments/owners across formats.
5. **Wave 4**: after scanning proves *zero supported authoritative SQL consumers*, retire adapters, pg backup/deploy files, SQL-only tests and dependencies. Replace PostgreSQL CI with durable Memory DB recovery/concurrency tests, then update protected-branch required checks and README in the same coordinated transition. Keep an isolated, auditable migration reader only while needed.

Every wave: before/after authoritative-owner inventory, imported record counts + deterministic digests, same-image restart, fault injection, snapshot restore, rollback/forward recovery, review and explicit user acceptance. Never delete the only valid legacy state as a shortcut.

## Commands and evidence

```bash
python scripts/sql_inventory.py --ref origin/master \
  --json-out artifacts/sql-inventory/master.json \
  --markdown-out artifacts/sql-inventory/master.md
python scripts/sql_inventory.py --base-ref origin/master --guard \
  --json-out artifacts/sql-inventory/head.json \
  --markdown-out artifacts/sql-inventory/head.md
python -m unittest -q tests.test_sql_inventory
```

`SQL dependency inventory` GitHub Actions workflow runs at the **exact PR head**, independently scans `origin/master` and the PR, uploads both JSON and Markdown evidence, and rejects any increase of direct runtime SQL signatures without rewriting the accepted legacy baseline.

**Caveats:** lexical SQL statements in comments/docstrings may be ambiguous; the inventory flags evidence rather than claiming execution. Dynamic imports/reflection, generated files or runtime configuration not tracked by Git require separate targeted tests and operational review. Files with non-UTF8/binary content are logged as skipped; absence of matches never proves a complete database migration.

**Safety:** This PR does not remove SQL, change CI-required checks or branch rules, merge, deploy, or cut over any storage.

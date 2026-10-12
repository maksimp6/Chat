# Issue #776 — Wave 1 transactional read-only SQL → shadow proof

**STATUS:** DRAFT / shadow-only; *never a production cutover*. Dependency: PR #1174, FileMemoryDB durability and `wave1_shadow` typed schema. Scope includes only `sessions`, `global_memory`, and `configs.memory_config`. SQL stays authoritative. This implementation does not update/erase SQL, forward any production traffic, change the running credentials backend, or merge anything.

## Transactional source

`migrations.wave1_sql_reader.SqlSnapshotReader` reads all three tables in one transaction, with fixed SELECT projections and a 100,000-row limit per table:

- **SQLite**: requires an existing non-symlink source file; connects with URI `mode=ro`; enables `PRAGMA query_only=ON` and `BEGIN`, then always `ROLLBACK`. In WAL mode, concurrent commits after the first SELECT cannot mix session and memory revisions. The reader never invokes SQL DDL/DML.
- **PostgreSQL**: requires a caller-owned fresh `autocommit=True` connection and begins `REPEATABLE READ READ ONLY` before selecting rows. It always rolls back and closes the supplied connection. Real PostgreSQL credentials/DSN are not accepted as arguments to this module. An adapter with non-idle transaction status is **refused**, even when autocommit is set. Production reader credentials must have database-level **SELECT-only** privileges; transaction read-only is defense in depth, not the sole security boundary. Never log credentials, SQL result values or owner roster.
- **Strictness**: missing tables/columns, malformed JSON, duplicate rows, a secret-bearing session metadata key or unsupported session/fact shape aborts the snapshot. No source content is dumped into errors.
- **Revision/digest**: `dialect:content-sha256:<hash>` identifies the contents observed, not a monotonic WAL/LSN. Two equal snapshots do not prove that no intervening writes occurred (ABA). True ongoing synchronization needs a trusted source change cursor and independent owner attestations.

## Owner binding is a release gate, not a fallback

Legacy `sessions` has no authoritative owner field. A caller may supply *both* an exact session-to-owner roster and the set of known owner IDs from an independently trusted owner store. The reader refuses missing/extra/mismatched entries. Even when the values match, `owner_audit.status` is explicitly **`externally_asserted_unverified`**: an ordinary mapping is not authenticated proof. Without mappings, status remains `unverified_missing`. Neither state allows authorization or cutover, and no owner information is injected into legacy `metadata_json`. The digest in the report is only for parity, not an ownership certificate.

Real owner binding must be resolved and independently audited before any runtime migration. Do not derive it from a session name, profile, bearer token or an untrusted metadata attribute. Credential values belong to #755 Secret Store.

## Bounded shadow catch-up and restart/restore

`shadow_catchup(reader, new_isolated_directory, max_attempts=1..3)`:

1. Reads one consistent SQL transaction into the typed `Wave1Snapshot` from PR #1174.
2. Stages into a **new isolated file** under shadow-only namespaces, with the manifest written last. Reopens the file and checks row parity, count and digest.
3. Creates an isolated backup and restores to a separate path; checks restored row parity, count and digest. Backup files are private `0700` directory and `0600` payload/manifest.
4. Reads SQL **again** in a new read-only transaction after staging and backup/restore. If source data or externally supplied owner audit changed, stage a new generation with no in-place update. On persistent churn, **fail closed** after the attempt limit.
5. Returns only `SHADOW_ONLY_UNVERIFIED_OWNER`, *never* `READY_FOR_CUTOVER` or `VERIFIED_PRODUCTION`. The report contains digests and counts, not raw SQL records.

This catches source drift between transactional snapshots; it does not tail a WAL stream or guarantee monotonic source revision. Side-effecting database maintenance, ownership resolution, runtime switching, and cleanup of old SQL stores are deliberately outside this PR.

## Test evidence and limits

- Real synthetic SQLite in WAL mode: concurrent writer between table reads, repeatable first snapshot, subsequent revision change, source data unchanged by reader, refusal of writes on `mode=ro` connection.
- Strict owner coverage, unknown owner, symlink source, secret metadata and missing schema denial.
- PostgreSQL adapter contract: verifies `BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY` and no DML; refuses non-autocommit sources. A separate test uses the repository PostgreSQL CI service when `ALICE_DATABASE_URL` is configured: it creates a uniquely named disposable test schema, performs a concurrent writer commit, and checks repeatable-read parity. If PostgreSQL is not configured, that integration case is explicitly skipped.
- Shadow: catches one source change by staging generation 2, refuses indefinite churn, rejects reuse of existing target, reopens staged data and verifies backup/restored state.
- No actual production SQL contents or credentials are read. No real owner approval, Cloud.ru filesystem power-loss test, KMS encrypted backup or no-mixed-authority cutover has been established.

## Next acceptance gates (not implemented here)

1. Independently verify ownership with trusted session→owner issuance and no missing records.
2. Complete independent PostgreSQL CI E2E review for each supported production schema version. The disposable CI schema test is a preliminary proof, not evidence about deployed databases.
3. Implement durable source change cursor and bounded catch-up window, with explicit rollback/reconcile of unacknowledged writes.
4. Verify isolated restore on the target Cloud.ru filesystem plus KMS backup policy; gate cutover on a user acceptance and separate release PR.
5. Only after protected-branch checks and authorized release: migrate runtime consumers one-by-one and retire SQLite/PostgreSQL under #776 Wave 4.

**NO MERGE · NO CUTOVER · No RDC or Redmi 9 changes.**

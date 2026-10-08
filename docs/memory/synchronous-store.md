# Synchronous Memory Store — implementation status

> **Development only — PR #1042 / Issue #776. NOT ACCEPTED / NOT DEPLOYED.**
> Not the default Alice Pro database; SQL remains in the application.
> This document describes reviewed code and evidence, not claimed production readiness.

## Goals and agreed architecture

Alice Pro targets one simple **file-native, in-process Python Memory DB**:
- private RAM dictionary for hot **committed** state and fast reads;
- typed application contracts rather than SQL or direct dictionary mutations;
- one writer, atomic transactions, compact append-only changes and **synchronous fsync before acknowledgement**;
- corruption detection, restart recovery and independent, restorable backups;
- a future **two-service RAM mirror for data integrity**, not two independent applications.

The second service and network replication are **not implemented here**. Under the future mirror contract, loss of a service must not permit isolated writes. Existing legacy SQL backups remain untouched and **will not be imported**. No Cloud.ru dependency.

## Code and responsibility map

| Component | Code present | What is not yet proven |
| --- | --- | --- |
| `memory_engine/__init__.py` | Exports `Store`, `MemoryStore`, `Transaction`, `Commit` and `StoreError` | No domain-specific records |
| `Store[ValueT]` protocol | Generic Python `Protocol`, typed `get/set/delete`, annotated arguments and return values | Runtime per-domain validators are not yet implemented |
| `MemoryStore` | Private RAM `dict`, copy-safe reads, process-level POSIX file lock | Not integrated into `db.py`/application |
| `Transaction` | Staged changed keys, rollback on exception, one journal commit; rejects nested or closed use | Crash/fault proof incomplete |
| Journal | Delta JSON records, monotonic sequence, chained SHA-256, fsync before ACK | No format epoch, bounded replay, checkpoints or mirror |
| `backup()`/`restore()` | Copy/verify/publish code exists | **Not functioning reliably while journal replay is broken** |
| Tests | Functional tests plus desired-state RED contracts | No exact-head GREEN for entire function |

Business features (Chat, Treasury, MCP) must use typed contracts and never reach into the private RAM dict or storage files. Future replica confirmation belongs behind the durability boundary. Trace must be a sanitized non-blocking observer, not part of commit success.


### Type annotations and value contracts

The public `Store[ValueT]` protocol is generic: consumers can annotate
`Store[str]`, `Store[dict[str, str]]`, or a domain-specific model type. It
requires string `namespace` and `key` arguments, returns `ValueT | None`
for reads and `None` for mutations. `Commit` exposes typed `sequence:
int` and `digest: str`. The implementation documents each public method's
commit, rollback, or copy-safety contract.

**Current limitation:** `MemoryStore` remains the untyped JSON-compatible
engine and uses `Any` internally for values. Generic Python annotations
do not by themselves validate untrusted on-disk data or guarantee that a
namespace contains only one domain model. Typed namespace adapters and
runtime validators belong to a later functional slice, before production
cutover.

The contract test `tests/test_memory_type_annotations.py` checks the public
generic API and its method hints. The existing `mypy --strict` ratchet
must also pass without increasing its baseline.

## Example of the proposed API

```python
from memory_engine import MemoryStore

with MemoryStore("/path/to/alice.memory") as store:
    store.set("settings", "theme", "gray")
    with store.transaction() as tx:
        tx.set("chats", "c1", {"title": "New chat"})
        tx.set("settings", "language", "ru")
    assert store.get("settings", "language") == "ru"
```

This is an API example, **not a statement that persistence has passed acceptance tests**.

## Current blocking defects and evidence

**P0 — journal replay fix pending verification.** At original PR #1042 head `14f5e723`, `MemoryStore._inspect_log()` lacked the existing-journal return path. The current branch adds frame iteration and returns verified state, sequence and digest. Fresh-process reopen and Backup/Restore must still pass **exact-head tests** before this is accepted. The fix alone is not proof of durability.

**P0 — exact-head CI failed.** [CI run 37761699150](https://github.com/maksimp6/Chat/actions/runs/37761699150) on `14f5e7230d78f5af607609b31465a9d5b3814fea`:
- Code rules: `mypy --strict` return-code ratchet increased to 2 from allowed 1.
- Application tests: format check failed on `tests/test_memory_store.py:320`, so the full suite did not run.
- PostgreSQL integration: failed; the job needs detailed failure attribution.
- CI required: failed because mandatory selected checks were unsuccessful.

Standalone Format, Security checks, CodeQL and Local launch smoke succeeded on this SHA, **which does not override CI failure**. Focused Memory DB tests on that exact head are not verified GREEN.

**Remaining:** bounded replay/checkpoints, proved crash/torn-tail/ENOSPC recovery, health/read-only and uncertain-commit handling, runtime domain validation, Trace, mirrored fsync policy, and measurements at meaningful dataset sizes. The public generic type contract is a static promise; `MemoryStore` currently accepts `Any` values and does not yet validate each domain schema. Supported runtime SQL consumers, legacy packages and PostgreSQL CI remain present. Some `tests/test_memory_*desired_state.py` checks intentionally fail until replacement of SQL and the old backend switch; do not weaken those tests simply to turn CI green.

## SQL-free CI fail-first gate

The CI workflow runs a lightweight **SQL absence (fail first)** preflight before
starting the expensive Application tests and PostgreSQL integration jobs when
the backend/database platform is selected. It executes the existing SQL-free
runtime and retired-backend-switch desired-state tests with `pytest -x`.

While SQL is still present, this job is **expected to fail**. Its failure must
prevent both heavy jobs from starting, and the aggregate `CI required` check
must fail as well. Never mark these tests xfail/skip, delete them, or relax
checks just to make CI green. Non-database platforms remain independent.

The preflight verifies absence of SQL, **not** correctness of Memory DB; after
it passes, the full functional, crash/recovery and integration tests still
have to run. The existing PostgreSQL job must be removed only after the
SQL-free cutover is implemented and replacement coverage is established.

## Standalone ownership boundary

The client creates and closes its own `MemoryStore(path)` instance.
The library does not register or expose a process-global database provider.
`DatabaseInfo` and `DatabaseInfoContract` are passive typed metadata
contracts only; there is no global `bind_database_info` or `database_info`
function in the engine.

The legacy Alice `memory_inspect` tool now fails closed until the Alice
client explicitly supplies a database instance. It does not open files or
discover database paths itself. This Alice-specific tool is not part of the
standalone library acceptance criteria. No diagnostic behavior is claimed
available yet.

## Acceptance checklist

1. Fix replay so a newly created store survives close/reopen in a **fresh process**; corrupt committed entries must fail closed.
2. Prove transaction isolation, one writer, no visibility or success acknowledgement before durable commit.
3. Prove verified backup to a new path, restore into isolated empty store, and further writes after restore. Reject corruption without damaging prior state.
4. Fault-inject fsync errors, ENOSPC, torn writes and crashes; distinguish ambiguous commits from successful ones.
5. Measure p50/p95/p99 RAM reads and sync writes plus recovery duration and journal size for representative workloads.
6. Replace supported SQL consumers with typed interfaces. Only then remove SQL dependencies, old switch, and PostgreSQL CI when substitute tests pass; do **not** import old backups.
7. Require exact-head code rules, format, tests (including `tests/test_memory_type_annotations.py`), CI and security checks, with code + tests + this documentation reviewed as one feature.
8. Owner functional acceptance WORKS / DOES NOT WORK remains separate from explicit merge authorization; no mandatory GitHub APPROVED policy, while any live branch protections still apply.

## Dependencies and immediate work

- **Depends on:** durable replay, failure-atomic commit/recovery proof and the subsequent replacement of SQL consumers.
- **Cannot yet:** deploy as Alice Pro's authoritative database, remove SQL, declare backup/restart reliable, or merge PR #1042.
- **Can do now:** repair replay, correct CI type/format failures, run focused regressions and crash tests **without Phone RDC**.
- **Deferred, not blocking v1 engine:** second-service mirror and automated task scheduler.

## References

- [Functional PR #1042](https://github.com/maksimp6/Chat/pull/1042)
- [Memory DB design Issue #776](https://github.com/maksimp6/Chat/issues/776)
- [Superseded source PR #1041](https://github.com/maksimp6/Chat/pull/1041)
- [Observed exact-head CI run](https://github.com/maksimp6/Chat/actions/runs/37761699150)

*Status snapshot: 2026-10-08. Refresh evidence when the branch HEAD changes.*

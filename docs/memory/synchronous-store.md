# Synchronous Kristall: functional core and Logos task adapter

The accepted owner contracts are [#1045](https://github.com/maksimp6/Chat/issues/1045)
and [#1046](https://github.com/maksimp6/Chat/issues/1046), within
[#776](https://github.com/maksimp6/Chat/issues/776). This focused implementation
contains one library copy, relevant tests, and a Logos consumer. Packaging,
PyPI, transport schemas, mirroring and build-performance work stay separate.

## Public contract

`Store[ValueT]` has one name/value API: `get(name)`, `set(name, value)`, and
`commit() -> int`. `Kristall` implements it. Names are nonempty strings;
missing names return `None`. JSON `null` is therefore indistinguishable from
missing through this API. Values are finite JSON scalars, lists and string-keyed
dictionaries. Tuples, non-string dictionary keys, nonfinite floats and arbitrary
objects fail commit validation before any journal write.

Container graphs are checked before staging copies: cycles, nesting beyond 64
levels and more than 100,000 nodes (including dictionary keys) raise `StoreError`
without replacing existing staging. Shared acyclic subtrees remain supported.
Replay enforces the same limits and preserves invalid source bytes. These limits
bound recursive copying/JSON encoding; the existing 1 MiB frame limit still applies.
Text in each value (including dictionary keys) has a combined 1 MiB UTF-8
budget, checked in small chunks before staging. Invalid Unicode and text beyond
that budget fail without replacing previous staging or blocking the writer.
Shared text is counted each time it appears in the serialized value. Frame
metadata/JSON escaping can still make a value within this budget exceed the
separate encoded-frame limit at commit.

`set()` copies into client-owned staging and `get()` reads that staging before
committed data. `commit()` validates all staged values and appends one durable
record before publishing committed RAM state. An empty commit returns the current
sequence. `close()` discards staging. Reads and input values are copy-safe.
Staging belongs to the client instance, not individual threads; concurrent
read-modify-write consumers must use `transaction()`.

```python
from kristall import Kristall

with Kristall("alice.memory") as store:
    store.set("settings/theme", "gray")
    assert store.commit() == 1
    with store.transaction() as tx:
        tx.set("settings/theme", "dark")
        tx.set("settings/language", "ru")
    assert store.get("settings/theme") == "dark"
```

A successful transaction context exit commits all changes once. Exceptions
roll back its private overlay. Nested transactions, direct mutations during a
transaction, cross-thread transaction handles and escaped closed handles fail.
A transaction requires no outstanding client staging. The public interface has
no namespace argument, automatic `set()` commit, or per-value version.

## Durability and recovery

The internal journal uses version-1 JSON frames with ordered sequences and chained
SHA-256 digests. Its namespace representation is a private format detail, not an
application API. Append and `fsync` precede acknowledgement; creation also fsyncs
the parent directory. A stable POSIX lock file protects one writer process, while
an in-process lock serializes commits and committed reads. Forked handles cannot
write or release their parent's writer lock. Supported platforms need POSIX
`fcntl` and a filesystem supporting file/directory fsync and atomic publication.

Startup verifies frames in order, reading one line at a time. Unknown formats,
malformed sequences, checksum failures and partial tails fail closed without
modifying the source. Replay is idempotent. Replay time still grows with journal
length; checkpointing and bounded recovery remain future work. SHA chaining
detects corruption, not an attacker able to rewrite the complete chain.

`checkpoint()` requires no staged values or active transaction. Under the writer
lock it verifies the source journal against confirmed RAM state, sequence and
digest, then writes and verifies a snapshot, fsyncs it, atomically replaces the
journal, and fsyncs the parent directory. The sequence stays unchanged; the digest
becomes the new snapshot-chain baseline. Corrupt, missing or changed source data
is preserved without replacement and blocks writes pending recovery. I/O errors
or caller cancellation once publication starts also block further writes until
verified reopen. An exception is not proof that replacement did not occur.

The complete snapshot frame is capped at 1 MiB. An oversized snapshot is rejected
without changing the valid journal or blocking ordinary commits. Checkpointing is
explicit, with no background timer or automatic recovery-time guarantee; large
states and a growing uncheckpointed journal still need future work. Compaction
also verifies the complete current source, so its work grows with journal length.

An append/fsync failure never acknowledges the staged transaction. Caller
cancellation during append or RAM publication also blocks further mutations:
incomplete RAM metadata must not be used to retry a possibly persisted frame.
If RAM publication was interrupted, committed reads and `last_commit` fail until
reopen, while `info().last_commit` returns `None` instead of partial metadata.
An append/fsync failure never acknowledges or publishes the staged transaction.
The disk outcome can be uncertain, so subsequent mutations fail with recovery
required. Reopen verifies disk evidence; an unacknowledged complete frame may
survive. No automatic retry, rollback of journal bytes, or silent truncation is
performed. Callers must not treat an exception as proof that a write is absent.

`backup()` verifies a consistent committed journal copy before publishing to a
new destination. It excludes staging and refuses overwrite, including destination
creation races. `restore()` validates before atomically publishing to an empty,
idle target; staging and existing committed data prevent restore. Backup targets
must be private to their owner. This is local backup evidence, not remote storage
replication. Historical SQL backups are untouched.

## Logos integration

`agent_memory.client` owns the application writer registry and explicit shutdown.
Canonical path aliases share one `Kristall`; the library has no Logos imports
or global database owner. Stop consumers before `close_memory_clients()`.

`agent_shell.TaskStore` uses a typed, versioned task aggregate through this adapter.
Task IDs, state and events commit together. FIFO claims are serialized; external
handlers run after the claim commits and outside the storage lock. Running tasks
found after restart fail as interrupted instead of automatically rerunning.
Multiple task repositories in one process share the writer. Another process is
refused rather than allowed to independently claim tasks.

For a new file-native task store:

```sh
python -m agent_shell --db ./shell.memory add --role docs-engineer --title Example --kind echo
python -m agent_shell --db ./shell.memory list
```

The existing CLI default path/environment selector is preserved to avoid silently
switching users with existing data to an empty database. Existing SQLite files
are rejected and preserved; automatic legacy task import is not implemented.
Use an explicit new path for isolated acceptance. Live legacy cutover needs a
verified import before rollout; this change does not authorize deployment.

## Verification and remaining scope

Independent core tests run with only pytest and the standard library, outside
the Logos conftest and requirements. Strict mypy checks the library. Regression
coverage includes fresh-process reopen, SIGKILL before append/after fsync/after
ACK, failed fsync without publication, invalid values, corruption, transactions,
writer ownership, backup and isolated restore. Logos tests cover task behavior,
concurrent runners, failed durable claim, and fresh-process interrupted-task
recovery. The dedicated Kristall workflow runs core verification independently.

Logos now uses the native Kristall writer for chat/messages/settings/config,
identity and ownership, sessions, agent memory, invocation/Trace, MCP selection,
local-agent jobs, environments, provider metadata/encrypted credentials, quotas,
Treasury and business workflows. Records are separate native names; mutations
use the shared write barrier. SQL drivers, connection adapters, SQL migrations,
SQL query tool and PostgreSQL CI/deployment dependencies are retired.

The Logos client adds detached prefix iteration and atomic deletion for these
repositories. `items()` rejects incomplete RAM state; delete on a fenced writer
fails even for a missing name. The application owns shared-writer shutdown;
stopping a preview runtime releases only that runtime's writer before removal.

Deployments require an explicit persistent `ALICE_MEMORY_PATH`, one writer
process and independently verified restart/restore evidence. Cloud object storage
remains backup, not the live filesystem. Repository implementation/testing does
not approve production data cutover, delete historical backups or prove remote
cloud persistence. No automatic SQL import or generic SQL compatibility adapter
is provided. Startup refuses an implicit empty native database beside existing
legacy data; an explicit native target belongs to separately verified activation. Full #776 cloud backup, large-state recovery and rollout acceptance
remain separate. Aggregate #1042 must not be merged wholesale.

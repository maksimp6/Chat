# Optional durable replication receiver

Refs #776 and #1214; owner contract: issue comment 6150175535.

`memory_engine.replication.ReplicaSink` accepts bounded, reassembled MR01 messages
authenticated with a caller-supplied 32-byte HMAC-SHA256 key. HMAC authenticates;
it does not encrypt. A future transport must provide confidentiality and peer
authorization. Neither this module nor its receipt is an authenticated network
ACK protocol. No sockets, source exporter, snapshot/resync, failover, key rotation
or connection manager are implemented in this first slice.

Use a dedicated native MemoryStore replica with one pinned source/epoch. Never
adopt an existing local database. Source changes must be exported only after their
authoritative commit; automated committed-source export remains future work.

Messages are `MR01 || HMAC(body) || canonical JSON body`. Body fields are `source`,
`epoch`, `sequence`, `previous`, `changes`. Canonical JSON uses sorted keys and
compact separators with ASCII escaping. Digest is SHA256 of the body. Changes
are unique-name `set`/`delete` operations. No empty batch, unknown field, gap,
authority change, conflicting retry or noncanonical body is accepted.

Data and the private inbound cursor are one native journal transaction. Receipt
`DURABLY_APPLIED` is returned only after native durability and RAM publication.
Retrying the last identical transaction returns the same receipt without another
data mutation or commit. The native `confirm_durable()` barrier re-syncs recovered
journal and parent-directory bytes before retry acceptance; failure fences the
writer. An earlier fsync failure may leave a complete recoverable transaction,
so recovery never assumes rollback. Concurrent duplicate delivery is serialized
by the existing writer lock. Local staging or a later local commit is rejected.

Deduplication retains only the latest cursor. Senders must serialize transactions
and retry that transaction until acknowledged. Older retries fail closed; this is
not a general exactly-once service, multi-writer replication or a sliding-window
transaction transport. Core journal limits apply in addition to wire-message
limits. Compaction/retention/snapshot lifecycle requires its own subsequent slice.

Tests cover durable reopen/lost ACK retry, malformed/authentication failures,
transaction atomicity, sequencing, divergence, failed fsync/reconfirmation,
concurrent duplicates, initial local-state rejection and later local mutation.
The independent core workflow includes these tests without Logos dependencies.
No hardware, network or live Memory DB replication claim follows from unit tests.

# #1413 D4: collection-independent knowledge preparation

Accepted contract: [Court remand D4](https://github.com/maksimp6/Chat/issues/1413#issuecomment-6366889282),
owner request to implement all shortcomings, and [scoped admission](https://github.com/maksimp6/Chat/issues/1413#issuecomment-6368155981).
Base develop: `2e1f1bcacfe6f4762b32c3e2e5b1114448199cc3`.
Primary implementation owner: Backend Engineer, `stable_cache`; publication and
independent review coordinated by root. Branch: `fix/1413-stable-knowledge-cache`.

The sealed context packet remains the collection/audit record. The indexed
derivative preserves all source text, source versions, hashes, revision bindings,
redaction counts, owner instructions and coverage. Only the top-level collection
time and packet seal move out of indexed content; each preparation receipt links
its original packet hash and collection time to the stable document hash.

The cache identity includes declared owner/access scope, repository, Yandex
project/store, processing version and chunking settings. Unknown owner/access
scope fails before provider access. These declarations do not prove provider ACLs
or authorize data transfer. Credentials and credential fingerprints are not part
of the document, filename, receipt or lock name. The existing workflow passes its
repository owner and repository scope explicitly.

A private advisory lock serializes cooperating processes and threads on the same
host, then every attempt verifies actual provider state through the existing
Files/Vector Store helpers. There is no new durable cache/database or indexer.
The lock is **not distributed across runner hosts**; the current `logos` workflow
concurrency group serializes that caller. No claim of account-wide coordination
or cross-host exactly-once upload is made. A retry after an upload/attachment but
failed indexing reuses confirmed provider state, and never deletes prior files.

Deterministic regression coverage checks repeated captures at different times,
source/owner/revision changes, owner/access/storage scope changes, processing and
index settings, tampered provider content, failed indexing followed by retry,
same-host concurrency, and unsafe lock paths. Counts observed in the offline
transport distinguish GET, upload, attachment, search and generation. They are not
live billing or proof of savings. Runtime transport-wide request counters are not
introduced by this slice. New model/provider runs, paid indexing, deployment,
credentials, policy changes and merge are outside the admitted scope.

Local Python 3.12 validation: 126 tests passed across context, knowledge,
dialogue-workflow and existing semantic Vector Store helpers; code rules: 109
passed. Two temporary negative-control mutations were detected and restored:
retaining collection metadata breaks the new recapture test; bypassing the lock
creates four uploads and breaks the concurrency test. Coverage measured with a
temporary configuration includes all changed executable Python lines; the
repository's normal coverage configuration omits scripts. No live operations were
performed. The initial alternate coverage command hit a stale branch/statement
data conflict; a separate coverage data file and explicit branch mode resolved it.

Final tested head, formatter gate, independent review and exact-head CI are
reported in the PR by root. Owner acceptance remains separate. This change does
not close D0–D11 or the hosted/Android acceptance requirements.

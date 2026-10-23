# Full repository evidence in the existing RAG

Canonical task: #1413, D1/D6. Owner instruction in this session: add the full
GitHub repository to RAG, retaining the launch prohibition. Verified develop:
`2e1f1bcacfe6f4762b32c3e2e5b1114448199cc3`. Implementation branch:
`feat/1413-full-repository-rag`. No work is added to the response repair #1441.

## Current task handoff

- Primary implementation owner/executor: root (Backend Engineer, this Codex session).
- Git collection: actual subagent `git_corpus`; GitHub collection: `github_corpus`.
- Independent review: actual subagent `evidence_scope`; owner acceptance remains separate.
- Accepted scope: the owner's explicit repository upload request plus #1413 comment
  6366889282. Deliverable: immutable source collection, visible omissions, sanitized
  upload to existing store, verified direct search; no new index service.
- Available tools: local Git/Python and authenticated GitHub, existing
  `semantic_pipeline` Files/Vector Stores transport. No local Yandex credentials;
  the repository workflow uses its existing secret bindings without exposing them.
- Allowed: repository reads, focused code/tests/draft PR, the requested RAG upload
  and direct searches. Generation/continuation budget is zero. No Logos execution,
  merge, permission/secret changes, store replacement or deletion.
- Stop: unavailable API permission/credential, mismatched source/store/hash,
  unexpected branch changes, read limits, or indexing failure. Preserve a partial
  receipt and explicit gaps; do not upgrade partial to complete.

## Collection and storage

`scripts/repository_rag.py` feeds the existing Yandex Files/Vector Store transport.
The collector has no generation entrypoint. It uses store
`fvtvp6t2su0g5q4ft2ev` by default; the live store identity must pass validation.

Git collection uses immutable objects for all fetched heads/tags/PR refs and
their reachable history. Commits and path changes preserve provenance; each blob
is archived once. Dirty/untracked working-tree files are excluded. Sensitive
paths, shallow/partial history, missing LFS payloads and submodules are explicit
gaps. An LFS pointer is not evidence that its payload was obtained.

GitHub collection inventories open/closed Issues and PRs, comments, reviews,
Discussions, Actions runs/attempts/jobs/logs/artifacts and releases. Public GitHub
file attachments retain links to their parent records. It records
access failures, expired sources, deferred request budgets and byte limits.
Available local HTTP cache supports continuation without duplicating completed
immutable reads. The API is not a transactionally frozen repository snapshot:
updates during collection remain possible and capture times are recorded.

Actions run inventory uses inclusive UTC-second `created` windows from repository
creation to a fixed cutoff immediately before the collection's starting second.
A reported count of 1000 or more splits the interval into disjoint children;
smaller leaves exhaust pagination and reconcile IDs, creation times and counts.
A saturated single second, changed counts, invalid rows and unfinished windows
remain explicit gaps. Window evidence is separate from the observed run-ID count;
it does not establish an atomic snapshot of mutable run records.

The private credential-scoped cache retains the epoch, pending intervals and
hash-verified observation pages. Later passes continue unfinished intervals and
reuse completed leaves within that epoch. An unfinished epoch resumes with its
saved cutoff; a completed traversal refreshes for a new collection epoch.
Corrupt state or pages are rejected and visibly recollected. Missing repository
creation time permits only an explicitly unverified unfiltered fallback.

`--github-passes` accepts 1–3 passes and defaults to one locally. Hosted repository
collection selects three, sharing the same private archive, credential-scoped
cache, progress cursor and cumulative 2 GiB download allowance. Git is collected
once; provider upload happens once after the passes. Each pass retains its timing,
source-version delta, cursor snapshots and gaps in `github-collection-passes.json`.
An actual authentication/rate-limit response or exhausted download allowance stops
later passes. So does no new source version combined with no cursor advancement;
empty review inventories can still advance the cursor, and splitting or completing
Actions windows advances the inventory frontier even before source rows appear.
Repeated failed attempts and timestamp changes alone are not progress. The per-pass request
limit is a configured bound, not an external failure. Earlier gaps remain visible
as observations, and exhausting the pass limit never proves corpus completeness.

Raw originals live in a private local content-addressed directory. They are not
uploaded as GitHub artifacts/caches. The sanitized view preserves actor/action
structure while masking recognized credentials and pseudonymizing email
addresses. This is a documented recognizer set, not proof that arbitrary personal
data has been anonymized. Binary originals are retained locally; supported ZIP
and TAR/gzip members are inspected without filesystem extraction. PDF text layers
use a bounded system decoder and preserve extractor provenance; images and drawing
geometry are not interpreted. Unsupported binary formats,
encrypted archives and missing external exports remain named gaps.

Capture timestamps are observations, excluded from document identity. Provenance
documents are separate from content. Provider packs carry a verifiable hash and
per-document offsets/hashes. Repeated runs verify and reuse existing packs; new
packs contain only new documents. Existing provider knowledge is not deleted.
Already attached historical packs can represent the unchanged portion of a new
snapshot; excluded historical documents are not counted as current evidence.
Mixed historical packs are never newly attached. File-level `completed` status
is required before reporting an indexed document.

## Run

```sh
git fetch origin '+refs/heads/*:refs/remotes/origin/*' '+refs/tags/*:refs/tags/*' '+refs/pull/*/head:refs/rag/pull/*'
git lfs fetch --all origin
python -m scripts.repository_rag --git-root . \
  --archive-dir /private/repository-rag \
  --receipt /private/repository-rag-receipt.json
# In the authorized environment with existing Yandex credentials:
python -m scripts.repository_rag --upload-only \
  --archive-dir /private/repository-rag \
  --receipt /private/repository-rag-receipt.json
```

Only one writer may use an archive directory. For hosted execution, the existing
`Semantic work` workflow has mode `repository`; its existing concurrency group
serializes jobs. It requires explicit upload scope and `allow_model_call=false`.
Mode `repository-check` inspects existing provider files and attachment statuses
without source collection, provider writes or generation. It does not assert that
candidate pack contents have been verified. Failure receipts retain the HTTP
method, sanitized API route and safe provider error code.
Read-only checks may run while the serialized writer is active. Mode
`repository-resume` accepts a prior successful check's `resume_run_id`, restores
its exact receipt from this repository, and verifies every saved pack and source
document before attaching anything. It reuses the provider files without Files
uploads. Restored sanitized copies are not reported as recovered raw originals.
Indexing admits at most eight pending files, polls file completion, and bounds
HTTP 429 retries and the overall wait; progress survives in a separate checkpoint.
It uploads only the receipt and omissions, never private raw originals. Hosted
raw evidence lasts only for that runner; this is not a durable archive or a
replacement for the separate Git LFS preservation requirement.

The observed unfiltered Actions API stopped at a declared 40,000 runs and returned
HTTP 422 on page 401. An older-time query reported another 2,500 runs; 40,000 is
not a complete inventory count. The separate artifact inventory reconciled
8,432 records, declaring 22,265,233,692 bytes before downloads. A configured
request/download budget cannot cover every payload in one execution and is not
evidence that the remaining sources are unavailable. `indexed_partial` is an honest
intermediate result, never completion of D1. The private local archive/cache is
the resumable path. Hosted invocations may repeat source reads because their raw
cache is deliberately not published. External saved provider responses and
permitted chat exports must be supplied as sources; their absence is explicit.

## Evidence and acceptance

The receipt separates collected source versions, prepared documents, provider
file IDs/status and actual search fragments. A random absent marker is checked
without generation. Missing source files, corrupted hashes, wrong repository or
store, wrapper-only matches and ambiguous historical quotes must not become
verified source attribution. Verbatim overlap proves provenance, not semantic
entailment or actual input delivered to Logos.

```sh
python -m pytest -q tests/test_repository_corpus_git.py \
  tests/test_repository_corpus_github.py tests/test_repository_rag.py
bash scripts/pre_push.sh
```

Offline controls are separate from live upload/search. A green workflow with
`indexed_partial` does not mean the full repository is indexed. Final D1
acceptance requires reviewing the per-source gaps, complete accessible-source
reconciliation, durable storage requirements and independent owner acceptance.

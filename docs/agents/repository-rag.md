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
Discussions, Actions runs/attempts/jobs/logs/artifacts and releases. It records
access failures, expired sources, deferred request budgets and byte limits.
Available local HTTP cache supports continuation without duplicating completed
immutable reads. The API is not a transactionally frozen repository snapshot:
updates during collection remain possible and capture times are recorded.

Raw originals live in a private local content-addressed directory. They are not
uploaded as GitHub artifacts/caches. The sanitized view preserves actor/action
structure while masking recognized credentials and pseudonymizing email
addresses. This is a documented recognizer set, not proof that arbitrary personal
data has been anonymized. Binary originals are retained locally; supported ZIP
members are inspected without filesystem extraction. Unsupported binary formats,
encrypted archives and missing external exports remain named gaps.

Capture timestamps are observations, excluded from document identity. Provenance
documents are separate from content. Provider packs carry a verifiable hash and
per-document offsets/hashes. Repeated runs verify and reuse existing packs; new
packs contain only new documents. Existing provider knowledge is not deleted.
File-level `completed` status is required before reporting an indexed document.

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
It uploads only the receipt and omissions, never private raw originals. Hosted
raw evidence lasts only for that runner; this is not a durable archive or a
replacement for the separate Git LFS preservation requirement.

GitHub currently reports roughly 40,000 runs and 8,399 artifacts. A request budget
cannot cover every payload in one execution. `indexed_partial` is an honest
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

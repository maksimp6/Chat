# Immutable Python repository index

This opt-in slice of #538 extends `scripts/build_ai_index.py`; it does not add a
second retrieval service, Redis, a model provider, or financial storage. The legacy
working-tree AST index remains available. `HybridRetriever` from #579 consumes the
same index shape and validates embedded snapshot identity when present.

## Build and reuse

Run from the repository checkout. The destination is a derived artifact, not a
source file. Choose the authorized repository explicitly; its name is caller-supplied
scope metadata, not a remote-origin/authentication check.

```sh
python scripts/build_ai_index.py \
  --root . --revision HEAD --repository maksimp6/Chat \
  --output /tmp/alice-python-index.json --compact

python scripts/build_ai_index.py \
  --root . --revision HEAD --repository maksimp6/Chat \
  --previous-index /tmp/alice-python-index.json \
  --output /tmp/alice-python-index.json --compact

python scripts/build_ai_index.py \
  --index-file /tmp/alice-python-index.json \
  --query-affected agent_retrieval/service.py --compact
```

The revision is resolved once to a full commit SHA. Tree enumeration and source
reads use Git objects (`ls-tree`, `cat-file --batch`), not mutable working-tree
files. Dirty, staged and untracked edits cannot be labeled as committed content.
Git replacement objects are disabled; filters/hooks and source modules are not run.
No clone, fetch, API call, model download or embedding request is invoked.

The index records normalized `owner/repo`, source commit, Git blob identity,
SHA-256 of source bytes, namespaced `source_id`, a commit-specific `source_ref`, and
line-span links for AST symbols. It stores compact AST facts, not source bodies or
string literal values. The normal exclusions still apply. Non-regular `.py`
entries, including symlinks, are skipped and reported in `skipped_paths`; their
targets are never read. This is not a guarantee that all identifiers/paths are
non-sensitive: the caller must authorize the indexed repository and recipients.

## Incremental contract

The previous JSON must be a trusted locally generated artifact, not arbitrary
untrusted input. It is a cache, not a signed source of truth. Repository, base
schema, snapshot schema and Python/parser version must match; otherwise rebuilding
without `--previous-index` is required. Bump the parser version when changing the
AST extraction semantics.

Unchanged `(repository, path, Git blob)` entries reuse a deep copy of the AST.
Changed and new files are parsed, removed files disappear, and all source links
are rebound to the selected commit, including links in reused entries. A full and
incremental build of the same snapshot produce identical JSON. Counters for
parsed/reused/deleted files go to stderr and are not embedded in that JSON.

Reuse avoids AST parsing and unchanged blob reads; it does not avoid tree
enumeration or guarantee a particular latency. The cache remains read-only to the
builder. Output is replaced atomically only after a complete successful build.
A failed build leaves an existing output untouched. Index files are rebuildable,
not a durability mechanism for operational or financial records.

## Bounds and failure behavior

Snapshot defaults: 5,000 files, 1 MiB per file, 32 MiB total source bytes; Git calls
have a 30-second timeout. Override the positive bounds using `--max-files`,
`--max-file-bytes`, and `--max-total-bytes`. Size limits are checked from tree
metadata before blob reads, including on a warm-cache run. Tree listing metadata
itself is not a streaming/memory-bounded parser; use only authorized repositories.

Invalid/unavailable revision, incompatible/malformed cache, unsupported source,
limit breach, or I/O failure returns a nonzero CLI status rather than partial
success. Raw Git stderr and source lines are not printed. No automatic paid
fallback exists.

## Query and retrieval freshness

For a snapshot, `query_affected()` takes the revision/repository from the artifact
and returns source links. A conflicting `--git-revision` is rejected. The legacy
index has no independently verified Git revision: its optional revision argument
is only a caller label, not proof of committed content.

Pass the snapshot and its matching external repository/head labels to the existing
`HybridRetriever`. Embedded repository, source kind, schema and commit must also
match; external labels cannot make a different snapshot current. Code hits expose
the immutable source link when available. Existing legacy-index behavior and
retrieval ordering remain unchanged.

The existing impact query is still a first-order Python-import heuristic. It is
not a complete dynamic dependency analysis, a safe reason to disable CI, or proof
that an unknown path has no affected tests. Frontend, issue/PR ingestion, chunked
full-text search, embeddings, remote cache service and relevance benchmarks are
outside this slice.

## Verification

```sh
python -m pytest -q tests/test_repository_revision_index.py \
  tests/test_ai_repository_index.py tests/test_agent_retrieval.py
bash scripts/pre_push.sh
```

Tests use real temporary Git repositories, not a substituted Git server. They
cover committed-vs-dirty input, repeat/change/add/delete, isolation and cache
versions, source references, secret literal omission, symlinks, size bounds,
explicit failures, atomic CLI output and retrieval freshness. These checks do not
claim that cloud embeddings or a production deployment have been verified.

Configured partial/promisor clones are deliberately rejected before source reads,
even when a particular requested blob happens to be present. This prevents an
implicit network fetch on Git versions that do not honor the lazy-fetch
suppression environment variable. Use a complete authorized local repository;
this command does not repair or expand the clone automatically.

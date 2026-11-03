# Repository in the RAG

The repository's git history and its GitHub data (issues, pull requests, comments, review comments, Actions runs) are appended to the owner's knowledge store by the app itself: the unit [`rag_sync`](../../rag_sync/README.md), the tool `rag` (actions `sync`, `status`, `reset`) and `POST /api/rag/sync`.

- **Only new data, in new files.** Each source keeps a cursor (the last stored commit, the `updated_at` / `created_at` of the last stored item); a sync reads only what is after it and writes it into new `logos-rag-<kind>-<time>-NNN.md` files. Stored files are never rebuilt. An edited issue is appended again as its new version.
- **Run.** The workflow `redmi9-rag-ingest` (manual) starts the sync in the app on the Redmi 9 and follows it; with the input `reset` it first removes every stored repository pack and the next sync loads the whole history again.
- **Replaced.** `scripts/rag_ingest_via_app.py`, `scripts/repository_rag.py`, `scripts/repository_rag_resume.py`, `scripts/repository_rag_verify.py` and `scripts/repository_corpus_*.py` were removed (2026-11-03); their packs (`gh-*`, `chat-repository-archive-*`) are removed by `reset`.
- **Checks.** Tests: `NOT_RUN` (AGENTS.md). A live run is the owner's command: `reset` + `sync`, then a second `sync` with nothing new writes no file, and one new commit writes one `logos-rag-git-*` file.

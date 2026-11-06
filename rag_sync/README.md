# Hadomi

`rag_sync` · Adds new commits and GitHub activity to chat file search.

Appends the repository's git history and its GitHub data to the owner's knowledge store (the store of `yandex_client_modules.tool_config.knowledge_vector_store_ids()`, the chat's File Search). Only new data is written, into new files; nothing stored is rebuilt.

**Sources.**

| Source | What a record is | Cursor |
| --- | --- | --- |
| git (`rag_sync.git_source`) | one commit: sha, parents, author, date, message, its changes against the first parent and the full text of every file version it adds or changes (a binary file by size, a credential file by name only) | the last stored commit; the next sync reads `<cursor>..HEAD` of the checkout in `LOGOS_LOCAL_REPO_DIR` (else this source tree) |
| GitHub issues and pull requests, issue comments, review comments (`rag_sync.github_source`) | one item as text | the `updated_at` of the last stored item (`since=`), oldest first |
| GitHub Actions runs | one run's metadata | the `created_at` of the last stored run (`created=>=`) |

An edited issue or comment is appended again as a record of its new version; the record of the old version stays. Secrets, tokens and e-mail addresses are masked (`rag_sync.redact`, moved from the replaced scripts).

**Files.** Records are packed into `logos-rag-<kind>-<UTC time>-NNN.md` of at most `PACK_LIMIT` (4 MiB, the size the replaced ingest used). Every record goes into the vector database as it is read. A full file moves its source's cursor past the records it holds, then is uploaded to Files and attached to the store; a file that fails ends the sync, and the next sync goes on after those records, so they are not embedded again. The ledger (Kristall `rag_sync/ledger`) keeps the cursors and every file written; `rag_sync/state` keeps the running or last sync. One sync runs at a time, in a background thread.

**GitHub token.** The token given to `POST /api/rag/sync` (used for that sync only, not stored), else `GITHUB_TOKEN`; without one the public API is used. A rate limit ends the sync with stage `rate_limited`; the next sync goes on from the cursor.

**Vector database.** Every record is also embedded into the app's own vector database (`vector_db`, port `vector_add`), one document per record in the collection `repository-<kind>` (`repository-git`, `repository-issues`, …), before its file is stored; the record's key is the document id, so a record written again replaces its document.

**Reset.** `reset` detaches and deletes every `logos-rag-*` file and the packs of the scripts this unit replaced (`gh-*`, `chat-repository-archive-*`), then forgets the cursors: the next sync appends the whole history again.

**Not covered.** GitHub returns at most 1000 Actions runs for one query, so a first sync stores the latest 1000 runs and later syncs the new ones. Commits of a side branch can be written twice when a pack ends between branches (`<cursor>..HEAD` names commits not reachable from the cursor); nothing is lost.

## Tool and HTTP

Category `rag`, one function `rag`, field `action`: `sync` (optional `sources`: `git`, `github`, `all`), `status`, `reset`. HTTP (`rag_sync.routes`, `/api/rag`): `POST /sync` (optional JSON `token`, 202, or 409 while a sync runs), `GET /status`, `POST /reset`. Logos uploads by itself: the model calls `rag` (the base instructions in `yandex_client_modules/mcp_mixin.py` name it for the knowledge base); the job `rag_vectors_check` of `redmi9-app-server.yml` also starts a sync.

## Ports

| Port | Provided by |
| --- | --- |
| `Config` | `core.config.Config` |
| `YandexClientError` | `yandex_client_modules.errors.YandexClientError` |
| `YandexResponsesClient` | `yandex_client_modules.client.YandexResponsesClient` |
| `get_store` | `storage.records.get_store` |
| `knowledge_vector_store_ids` | `yandex_client_modules.tool_config.knowledge_vector_store_ids` |
| `vector_add` | `vector_db.store.add` |

## Install

`composition/wiring.py` registers the host (`rag_sync`), `composition/tool_sources.py` adds the category `rag`, `composition/web.py` registers `rag_bp`.

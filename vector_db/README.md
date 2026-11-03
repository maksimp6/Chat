# `vector_db`

The app's own vector database: texts are embedded by the Yandex embeddings API (`yandex_client_modules.embeddings.embed_texts`) and kept and searched on the server, in the Kristall store. No provider Vector Store is involved.

**Two models.** Stored chunks are embedded with the document model `text-embeddings-v2-doc`, queries with the query model `text-embeddings-v2-query` (the provider's pair made to be compared), both at 768 dimensions, their largest size. A search compares only chunks of the document model at the query's size.

**Kristall has no search.** It is a key/value store with prefix listing; a search reads every chunk of the asked collections and ranks them by cosine similarity in this unit.

**Traces.** Every `add` and `search` passes the tool call's execution trace to `embed_texts` (the provider key is recorded there) and writes an event: `vector_db_add` (collection, id, chunks, tokens, model, dimensions) and `vector_db_search` (collection, chunks searched, results, top score, tokens, model).

**Add.** `vector_db.store.add(collection, text, doc_id=None, metadata=None, trace=None)` splits the text into chunks (paragraphs joined up to `CHUNK_CHARS` = 2000 characters, a longer paragraph cut at that size), embeds every chunk with the document model and keeps it as `vector_db/<collection>/<doc id>/<chunk>` with its text, metadata and vector. The same id replaces the document; without an id the text's hash is the id.

**Search.** `search(query, collection=None, k=None, trace=None)` embeds the query with the query model and ranks every chunk of the collection (of every collection when none is named) by cosine similarity; without `k` every chunk is returned, best first.

**Cost.** Every embedded text is one paid provider request; each call returns the tokens it spent.

Also `delete(collection, doc_id)`, `drop(collection)`, `collections()`.

## What is in it

- `repository-git`, `repository-issues`, `repository-issue_comments`, `repository-review_comments`, `repository-runs`: every record `rag_sync` appends (the app's code and history, its GitHub data).
- `dialogs`: every stored model reply with its question (`chat.mcp_routes._embed_turn`, in a thread after the reply is stored; a failure is a Court case `dialog_embedding_failed`).

## Tool

Category `vectors`, one function `vectors`, field `action`: `add`, `search`, `delete`, `drop`, `collections`. The base instructions (`yandex_client_modules/mcp_mixin.py`) name it, so Logos uses it by itself.

## Ports

| Port | Provided by |
| --- | --- |
| `embed_texts` | `yandex_client_modules.embeddings.embed_texts` |
| `get_store` | `storage.records.get_store` |

## Install

`composition/wiring.py` registers the host (`vector_db`), `composition/tool_sources.py` adds the category `vectors`.

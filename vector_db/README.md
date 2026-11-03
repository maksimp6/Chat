# `vector_db`

The app's own vector database: texts are embedded by the Yandex embeddings API (`yandex_client_modules.embeddings.embed_texts`) and kept and searched on the server, in the Kristall store. No provider Vector Store is involved.

**Add.** `vector_db.store.add(collection, text, doc_id=None, metadata=None)` splits the text into chunks (paragraphs joined up to `CHUNK_CHARS` = 2000 characters, a longer paragraph cut at that size), embeds every chunk with the document model (`text-search-doc`) and keeps it as `vector_db/<collection>/<doc id>/<chunk>` with its text, metadata and vector. The same id replaces the document; without an id the text's hash is the id.

**Search.** `search(query, collection=None, k=None)` embeds the query with the query model (`text-search-query`, the pair made to be compared with the document model) and ranks every chunk of the collection (of every collection when none is named) by cosine similarity; without `k` every chunk is returned, best first.

**Cost.** Every embedded text is one paid provider request; each call returns the tokens it spent.

Also `delete(collection, doc_id)`, `drop(collection)`, `collections()`.

## Tool

Category `vectors`, one function `vectors`, field `action`: `add`, `search`, `delete`, `drop`, `collections`. The base instructions (`yandex_client_modules/mcp_mixin.py`) name it, so Logos uses it by itself.

## Ports

| Port | Provided by |
| --- | --- |
| `embed_texts` | `yandex_client_modules.embeddings.embed_texts` |
| `get_store` | `storage.records.get_store` |

## Install

`composition/wiring.py` registers the host (`vector_db`), `composition/tool_sources.py` adds the category `vectors`.

# Remuza

`vector_db` · Embeds texts with the local embedding model and searches them by meaning.

The app's own vector database: texts are embedded by Ollama's `qwen3-embedding:0.6b` on the owner's PC (`ollama_client.embed`, Logos has no Yandex API since 04.11.2026) and kept and searched on the server, in the Kristall store. No provider Vector Store is involved.

**One model.** Stored chunks and queries are embedded by the same model (`DOC_MODEL` = `QUERY_MODEL`), at its own size (1024 dimensions). A search compares only chunks of that model at the query's size: chunks embedded earlier by the Yandex models (`text-embeddings-v2-doc`, 768 dimensions; most of `repository-git`) are kept but not found until they are embedded again.

**Packed vectors.** Kristall keeps every value in RAM and a listing copies it, so each chunk keeps its vector as `vector_f32`: the vector scaled to unit length, little-endian float32 in base64 (5.5 KB instead of 1,024 Python floats, ~32 KB in RAM). A score is then one `math.sumprod` of two unit vectors, which is their cosine. Chunks stored before (a `vector` list) are rewritten once, 50 per transaction, in a background thread started by the first `add` or `search` after start; until then search reads both forms.

**Kristall has no search.** It is a key/value store with prefix listing; a search reads every chunk of the asked collections and ranks them by cosine similarity in this unit.

**Traces.** Every `add` and `search` passes the tool call's execution trace to `embed_texts` (the provider key is recorded there) and writes an event: `vector_db_add` (collection, id, chunks, tokens, model, dimensions) and `vector_db_search` (collection, chunks searched, results, top score, tokens, model). `remember` searches without `k` (its `vector_db_search` shows results = searched) and writes `remember_results`: chunks searched, `given` (collection, id, chunk and score of each chunk read, best first) and `repeats_left_out`. Sense extraction writes `sense_extracted`: the documents read, the candidate phrases, the phrases chosen with their scores, and the tokens. The model gets only the text, so these events are the record of where it came from.

**Add.** `vector_db.store.add(collection, text, doc_id=None, metadata=None, trace=None)` splits the text into chunks (paragraphs joined up to `CHUNK_CHARS` = 2000 characters, a longer paragraph cut at that size), embeds every chunk with the model and keeps it as `vector_db/<collection>/<doc id>/<chunk>` with its text, metadata, model, dimensions and packed vector. The same id replaces the document; without an id the text's hash is the id.

**Search.** `search(query, collection=None, k=None, trace=None)` embeds the query with the same model and ranks every chunk of the collection (of every collection when none is named) by cosine similarity; without `k` every chunk is returned, best first.

**Cost.** Embedding runs on the owner's PC (no paid provider); each call returns the tokens it spent.

Also `delete(collection, doc_id)`, `drop(collection)`, `collections()`.

## What is in it

- `repository-git`, `repository-issues`, `repository-issue_comments`, `repository-review_comments`, `repository-runs`: every record `rag_sync` appends (the app's code and history, its GitHub data).
- `dialogs`: every stored model reply with its question (`chat.mcp_routes._embed_turn`, in a thread after the reply is stored; a failure is a Court case `dialog_embedding_failed`).

## Tool

Category `vectors`, one function `vectors`, field `action`: `add`, `search`, `delete`, `drop`, `collections`. The base instructions (`yandex_client_modules/mcp_mixin.py`) name it, so Logos uses it by itself.

## Remember

`remember` is the local model's memory tool (`ollama_client.settings.TOOLS`): no parameters; it searches every collection with the model's latest thinking. It gives back `context`, the sense of what it found as one short text (below), and `searched`. Stored chunks stay whole. A chunk already read in the same turn is left out, since the turn's prompt still holds its sense; when every chunk found was, `context` is empty and `note` says so. It gave 5 whole chunks of up to 2,000 characters before, ~2,500 tokens a call.

## Sense extraction

Owner, 05.11.2026: "we don't need full sentences from remember just do sense extraction, and convert it to text". `vector_db.sense` uses only the embedding model. The best chunk of each of the `DOCUMENTS` = 3 best-matching documents is cut into phrases at its punctuation (at most `PHRASE_WORDS` = 6 words, filler words dropped from their ends), every phrase is embedded, and the `SENSE_PHRASES` = 8 phrases nearest in meaning to the question, without near-repeats (`REPEAT_SIMILARITY` = 0.9), are joined with `; `, best first. Nothing is generated: every word is a stored word. On the owner's PC 32 short phrases embed in ~6 s.

`sense.answer(question, exclude_conversation=None, trace=None)` is the same for a question: the pinned dialog «🧬 База эмбеддингов» (`chat.mcp_routes`, conversation `logos-vectors`) answers with it, so no language model runs in that dialog. Its turns are stored in `dialogs` like any others and left out of its own answers.

## HTTP

`POST /api/vectors/<action>` (`add`, `search`, `delete`, `drop`, `collections`) with the tool's fields as JSON.

## Ports

| Port | Provided by |
| --- | --- |
| `embed_texts` | `composition.wiring._vector_db`: `ollama_client.embed` on `ollama_client.settings.URL` |
| `get_store` | `storage.records.get_store` |

## Install

`composition/wiring.py` registers the host (`vector_db`), `composition/tool_sources.py` adds the category `vectors`, `composition/web.py` registers `vectors_bp`.

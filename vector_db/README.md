# Remuza

`vector_db` · Embeds texts with the local embedding model and searches them by meaning.

The app's own vector database: texts are embedded by Ollama's `qwen3-embedding:0.6b` on the owner's PC (`ollama_client.embeddings.embed_texts`, Logos has no Yandex API since 04.11.2026) and kept and searched on the server, in the Kristall store. No provider Vector Store is involved.

**One model.** Stored chunks and queries are embedded by the same model (`DOC_MODEL` = `QUERY_MODEL`), at its own size (1024 dimensions). A search compares only chunks of that model at the query's size: chunks embedded earlier by the Yandex models (`text-embeddings-v2-doc`, 768 dimensions; most of `repository-git`) are kept but not found until they are embedded again.

**Packed vectors.** Kristall keeps every value in RAM and a listing copies it, so each chunk keeps its vector as `vector_f32`: the vector scaled to unit length, little-endian float32 in base64 (5.5 KB instead of 1,024 Python floats, ~32 KB in RAM). A score is then one `math.sumprod` of two unit vectors, which is their cosine. Chunks stored before (a `vector` list) are rewritten once, 50 per transaction, in a background thread started by the first `add` or `search` after start; until then search reads both forms.

**Kristall has no search.** It is a key/value store with prefix listing; a search reads every chunk of the asked collections and ranks them by cosine similarity in this unit.

**Traces.** An `add` or `search` of the `vectors` tool and the search of `remember` pass the tool call's execution trace and write an event: `vector_db_add` (collection, id, chunks, tokens, model, dimensions) and `vector_db_search` (collection, chunks searched, results, top score, tokens, model). A stored turn, a `rag_sync` record, a python run and `POST /api/vectors/<action>` pass none and write none. `remember` searches without `k` (its `vector_db_search` shows results = searched) and writes `remember_results`: chunks searched and `given` (collection, id, chunk and score of each chunk read, best first). Sense extraction writes `sense_extracted`: the documents read, the number of candidate phrases, the phrases given with their scores and a `key` (a hash for this process, by which a repeat is recognised: the trace redacts a phrase that looks like a secret, so its text would not match), `repeats_left_out` (chosen, but already given in this turn), and the tokens. The model gets only the text, so these events are the record of where it came from.

**Add.** `vector_db.store.add(collection, text, doc_id=None, metadata=None, trace=None)` splits the text into chunks (paragraphs joined up to `CHUNK_CHARS` = 2000 characters, a longer paragraph cut at that size), embeds every chunk with the model and keeps it as `vector_db/<collection>/<doc id>/<chunk>` with its text, metadata, model, dimensions and packed vector. The same id replaces the document; without an id the text's hash is the id.

**Search.** `search(query, collection=None, k=None, trace=None)` embeds the query with the same model and ranks every chunk of the collection (of every collection when none is named) by cosine similarity; without `k` every chunk is returned, best first.

**Cost.** Embedding runs on the owner's PC (no paid provider); each call returns the tokens it spent.

Also `delete(collection, doc_id)`, `drop(collection)`, `collections()`.

## What is in it

- `repository-git`, `repository-issues`, `repository-issue_comments`, `repository-review_comments`, `repository-runs`: every record `rag_sync` appends (the app's code and history, its GitHub data).
- `dialogs`: the reply of every finished chat turn with its question (`chat.mcp_routes._embed_turn`, in a thread after the reply is stored; a failure is a Court case `dialog_embedding_failed`).

## Tool

Category `vectors`, one function `vectors`, field `action`: `add`, `search`, `delete`, `drop`, `collections`. The Yandex client's base instructions (`yandex_client_modules/mcp_mixin.py`) name it; the local chat model is not offered it and reaches the database through `remember`.

## Remember

`remember` is the local model's memory tool (`ollama_client.settings.TOOLS`): no parameters; it searches every collection with the model's latest thinking. It gives back `context`, the sense of what it found as one short text (below), and `searched`. Stored chunks stay whole. A phrase already given in the same turn is left out of `context`: the phrases are chosen as for a first call and the repeats dropped afterwards, so a new thought can bring up other phrases of the same chunks, and the same thought gets an empty `context` and a `note` saying so (turn 1df336e8 got the same chunks three times). It gave 5 whole chunks of up to 2,000 characters before, ~2,500 tokens a call.

## Sense extraction

Owner, 05.11.2026: "we don't need full sentences from remember just do sense extraction, and convert it to text". `vector_db.sense` uses only the embedding model. The best chunk of each of the `DOCUMENTS` = 3 best-matching documents is cut into phrases at its punctuation; a chunk with nothing to add to the question, or a word-for-word copy of a chunk already read, takes no place among the three. A phrase is `PHRASE_WORDS` = 6 words with the filler words dropped from its ends. A word alone joins the phrase before it (`зовут кота Барсик`, `Имя Максим`, a colon ends nothing), which makes that phrase one word longer; after a phrase already longer than `PHRASE_WORDS` it stays a phrase of its own. A negation is never trimmed, and because it can act on the words on either side, a clause that holds one is not cut, however long (`Запускать тесты на этом устройстве без отдельной команды нельзя`), and a hard-wrapped line that ends in one goes on in the next line. A phrase with a negation takes no word alone after a comma, a semicolon, a dash or a sentence end: `Порт не 8080, а 9090.` gives `Порт не 8080` and `9090`. A quote, a bracket, a question mark and a line end part nothing: `система не «убивает» агента` stays one phrase, and so a word in a bracket can fall under the negation before it: `Порт не 8080 (а 9090).` gives `Порт не 8080 9090`. A comma, or a quote of several words, can still part a negation from what it negates: `Нельзя запускать, менять и удалять тесты.` gives `Нельзя запускать` and `менять и удалять тесты`. A phrase the question itself holds is left out, except a word alone after another phrase, which can be the answer; every other phrase is embedded, and `SENSE_PHRASES` = 8 phrases are chosen one by one: the nearest in meaning to the question, each losing `REPEAT_PENALTY` = 0.6 of its likeness to those already chosen (maximal marginal relevance). They are joined with `; `, best first. Nothing is generated: every word is a stored word, without the punctuation around it (the sign of `-5`, the dot of `.env` and the underscore of `_chat_turn` stay). On the owner's PC 32 short phrases embed in ~6 s, so an answer takes 10–25 s.

`sense.answer(question, exclude_conversation=None, trace=None)` is the same for a question: the pinned dialog «🧬 База эмбеддингов» (`chat.mcp_routes`, conversation `logos-vectors`) answers with it, so no language model runs in that dialog and the Court's censor, which judges a model's reply, does not read it (`court/README.md`). Its turns are stored in `dialogs` like any others and left out of its own answers.

## HTTP

`POST /api/vectors/<action>` (`add`, `search`, `delete`, `drop`, `collections`) with the tool's fields as JSON.

## Ports

| Port | Provided by |
| --- | --- |
| `embed_texts` | `ollama_client.embeddings.embed_texts` |
| `get_store` | `storage.records.get_store` |

## Install

`composition/wiring.py` registers the host (`vector_db`), `composition/tool_sources.py` adds the category `vectors`, `composition/web.py` registers `vectors_bp`.

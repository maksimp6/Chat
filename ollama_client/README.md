# Mokira

`ollama_client` · Serves chat and embeddings of the local models through Ollama.

Ollama on the owner's PC: the chat client of a dialog, embeddings for the vector database, and the settings and live state shown in ⚙️. It knows only Ollama and is independent of the Yandex client. Answers come back in the Responses API shape, so the chat tool loop, parsers and traces read them like any provider answer. Ollama keeps no conversation: the caller sends the whole dialog every time.

## Role in a dialog

Where this unit sits in the [life of a dialog](../docs/architecture/modules.md#life-of-a-dialog):

- **Send a message (the turn)**: `POST /api/chat`

## Public API

`from ollama_client import …`: `OllamaClient`, `OllamaClientError`, `OllamaError`, `ask`, `chat_messages`, `chat_tools`, `embed`, `settings`, `status`.

| Module | Purpose |
| --- | --- |
| [`ollama_client.chat`](chat.py) | One chat request to Ollama's `/api/chat`, with Responses API input and output. |
| [`ollama_client.client`](client.py) | `OllamaClient`, the turn requests of a dialog served by a local model: `ask`, `ask_with_mcp` and the `extract_*` readers the chat's tool loop calls. |
| [`ollama_client.embeddings`](embeddings.py) | Embeddings from Ollama's `/api/embed`. |
| [`ollama_client.settings`](settings.py) | The server, the models, their context, their tools and their instructions. |
| [`ollama_client.status`](status.py) | The settings and what Ollama reports right now. |

Other units reach it only through their own ports:

| Provides | To |
| --- | --- |
| `ollama_client.client.OllamaClient` | `chat` |
| `ollama_client.embeddings.embed_texts` | `vector_db` |
| `ollama_client.settings.MODELS` | `chat` |
| `ollama_client.status.status` | `conversations` |

`core/config.py` imports `ollama_client.settings` directly to add `MODELS` to the model catalog.

## Settings

All values live in [`settings.py`](settings.py), fixed and read-only. `GET /api/ollama/settings` (`conversations.routes`) returns them with the server's state, and ⚙️ shows them on the Ollama tab.

| Name | What it holds |
| --- | --- |
| `URL` | The address of Ollama on the owner's PC. |
| `MODELS` | The models Logos serves: the Logos key and its catalog entry (`upstream` is Ollama's name). |
| `CONTEXT`, `NUM_PREDICT`, `NUM_BATCH` | The `/api/chat` options of every request; `NUM_PREDICT` = -1 sets no output cap. |
| `THINK` | Thinking for a model whose `MODELS` entry names none. |
| `TOOLS` | The tool categories a local model is given. |
| `INSTRUCTIONS` | Ollama's own instructions, ahead of every turn. |

## Ports

None: `ollama_client` has no `host.py`. It uses only the standard library and `requests`.

## Install

Nothing to register: `composition/wiring.py` imports it for the ports above. Ollama itself runs on the owner's PC.

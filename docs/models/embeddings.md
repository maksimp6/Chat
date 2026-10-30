# Yandex embeddings

`POST /api/embeddings` uses Yandex's OpenAI-compatible
`https://ai.api.cloud.yandex.net/v1/embeddings` endpoint. It resolves the active
deployment credential for each application request, including issued or rotated
credentials, and sends its project in the provider headers.

| Model key | URI suffix | Dimensions |
| --- | --- | --- |
| `text-search-doc` | `text-search-doc/latest` | 256 |
| `text-search-query` | `text-search-query/latest` | 256 |
| `text-embeddings-v2-doc` | `text-embeddings-v2-doc/latest` | 128, 256, 512, 768 |
| `text-embeddings-v2-query` | `text-embeddings-v2-query/latest` | 128, 256, 512, 768 |

The full URI is `emb://<active-project>/<suffix>`. These models are listed in the
separate `embedding` section of `/api/models`, not in the chat model selector.

The request accepts exactly one of `text` (a nonempty string) or `texts` (a list
of nonempty strings), and either `model` or the legacy `mode: "doc" | "query"`.
The default is `text-search-doc`. For v2 models, optional `dimensions` selects one
of the documented dimensions; omission uses the provider default of 256.
Dimensions cannot be changed for v1. Unknown fields are rejected instead of
being silently discarded.

The application sends one provider request per text because the provider allows
only one text per embeddings request. Its existing application batch maximum is
64 texts, with a 60-second aggregate request budget. No text is truncated, and no
character count is used as a substitute for the provider's token limit. An error
after part of a batch reports `completed` and `tokens_spent`; it never retries a
paid request automatically. A valid provider reply must contain one finite vector
with the requested dimension. Responses retain the existing `embedding` or
`embeddings`, `tokens`, and `cost` fields and also expose `dimensions`.

Sources, checked 27 October 2026:

- https://aistudio.yandex.ru/ru/docs/ai-studio/concepts/embeddings
- https://aistudio.yandex.ru/ru/docs/ai-studio/api/Embeddings/createEmbedding

Provider calls, tests, compilation, and workflows: `NOT_RUN`.

# `agent_retrieval`

Token-bounded, deterministic hybrid retrieval for agent task packets over the context cache, shared memory and repository indexes, with BM25-style scoring and telemetry.

## Public API

`from agent_retrieval import …`: `HybridRetriever`, `RetrievalBundle`, `RetrievalHit`, `RetrievalQuery`, `record_retrieval`.

| Module | Purpose |
| --- | --- |
| [`agent_retrieval.legacy_models`](legacy_models.py) | Retrieval contracts used by retriever.py and sources.py. |
| [`agent_retrieval.models`](models.py) | Contracts for bounded agent retrieval. |
| [`agent_retrieval.retriever`](retriever.py) | Deterministic BM25-style hybrid retrieval and telemetry. |
| [`agent_retrieval.service`](service.py) | Token-bounded hybrid retrieval for agent task packets. |
| [`agent_retrieval.sources`](sources.py) | Retrieval source adapters for memory, deterministic code indexes and summaries. |

## Ports

None. The unit imports only the standard library, third-party packages and its own modules.

## Install

- Nothing to wire: import the modules you need.

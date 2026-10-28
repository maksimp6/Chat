# `agent_context`

Deterministic task-context packets for Logos agents: GitHub-first task contracts and an exact-head context cache that reuses evidence selectively.

## Public API

`from agent_context import …`: `CacheLookup`, `ContextSlice`, `EvidenceRef`, `EvidenceVersion`, `TaskContextCache`, `TaskPacket`, `TaskScope`, `record_context_cache_lookup`.

| Module | Purpose |
| --- | --- |
| [`agent_context.cache`](cache.py) | Exact-head task context cache with selective evidence reuse. |
| [`agent_context.models`](models.py) | Canonical, GitHub-first task packet contracts for agent context. |

## Ports

None. The unit imports only the standard library, third-party packages and its own modules.

## Install

- Nothing to wire: import the modules you need.

# Cloud.ru AI Agents integration research

This document records the integration facts used by the native Agent Gateway design.

## Verified API surface

Cloud.ru AI Agents exposes a Public API for information about AI agents, agent systems and MCP servers, including create/delete operations. API access requires authentication. The documented flow is service account -> API key -> IAM token, then `Authorization: Bearer <TOKEN>` on API requests.

Source: https://cloud.ru/docs/ai-agents/ug/topics/api-ref

## A2A

Cloud.ru AI Agents documents Agent-to-Agent (A2A) as a supported protocol for interaction between independent agents. The documentation describes Agent Cards, tasks, messages, artifacts, JSON-RPC 2.0, HTTP+JSON/REST and SSE streaming.

Source: https://cloud.ru/docs/ai-agents/ug/topics/concepts__protocols-a2a

The current A2A specification defines `message/send` as a JSON-RPC method over HTTP POST. The agent endpoint is discovered from the Agent Card. Authentication is carried by HTTP headers rather than inside the A2A message payload.

Source: https://a2a-protocol.org/v0.3.0/specification/

## Design decision for Alice Pro

Alice Pro should not embed a Cloud.ru/EvoClaw-specific Python SDK in the Android application. Instead:

1. Android talks to the Alice Pro backend.
2. The Agent Gateway owns provider adapters.
3. A2A is the first remote-agent transport adapter.
4. Provider credentials stay in the backend credential store and are never written to Execution Trace.
5. Provider-specific API management can be added separately from agent invocation.
6. MCP remains a separate adapter/transport for tool servers.

This keeps the Android application independent of provider SDK build requirements while allowing external agents to be connected later.

# Conversation-agent routing foundation

Conversation agents are immutable configurations identified by the tuple
`(runtime_id, conversation_id, agent_id)`. Selection is explicit for every
invocation; there is no process-wide current or default agent. This keeps two
agents with the same identifier in different preview runtimes isolated.

`InvocationContext` carries the selected `agent_id` and `runtime_id` as
request-scoped correlation fields. `ExecutionTrace` records those fields and
emits events for agent selection, handoff, and tool invocation. Tool arguments
are deliberately excluded from routing events to avoid copying secrets into
the trace.

Tools are reached only through the `agent.tool.invoke` operation on
`RuntimeDispatcher`. A caller cannot invoke a tool with an agent owned by a
different runtime or conversation. The operation handler is intentionally not
registered by this foundation: the MCP/plugin workstream tracked by #350 must
register it and connect it to `UniversalToolExecutor`, which remains the tool
policy and execution boundary. Implementations must not add direct MCP access
or a per-preview localhost/container fallback.

This slice does not add persistent user-agent CRUD or database migration. A
later storage layer can hydrate `ConversationAgent` values after enforcing
authenticated user ownership, while retaining this runtime routing contract.

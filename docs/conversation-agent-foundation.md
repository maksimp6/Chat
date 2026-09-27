# Conversation-agent foundation

Conversation agents are immutable identities scoped by the tuple
`(runtime_id, conversation_id, agent_id)`. Selection is explicit: the router
has no process-global current agent and no fallback agent. `InvocationContext`
carries the same three identifiers, and mismatches fail before routing.

Agent handoffs emit `conversation_agent_handoff` events. Tool requests emit a
correlated `conversation_agent_tool_invocation` event and then dispatch the
`agent.tool.invoke` operation through `RuntimeDispatcher`. The operation's
integration must call `UniversalToolExecutor`, so existing authorization,
approval, and transport policy remains the execution boundary.

## Deferred integration dependencies

This slice does not register `agent.tool.invoke` or access an MCP/plugin
registry directly. The MCP/plugin workstream must supply that dispatcher
operation and adapt its registry to `UniversalToolExecutor`. Conversation
storage must construct the agent identity from its trusted runtime and stored
conversation assignment; an LLM-provided `agent_id` is never authoritative.

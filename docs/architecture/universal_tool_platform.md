# Universal Tool Platform

Alice Pro uses one provider-agnostic contract between a tool registry and every
transport adapter.

## Flow

    Provider / UI / MCP adapter
              |
              v
       UniversalToolCall
              |
              v
        Tool Registry
              |
              v
    Validation -> Authorization -> Policy -> Approval
              |
              v
        Universal Executor
         |       |       |
       local    MCP    remote agent
              |
              v
       Result normalization
              |
              v
        ExecutionTrace

## Tool contract

Every registry entry is normalized to:

- name
- title
- description
- inputSchema
- outputSchema
- capabilities
- risk_level
- read_only
- requires_approval
- supported_transports
- executor
- metadata

Legacy tools can continue to declare parameters and func. The registry maps
those fields into the universal contract without requiring a simultaneous
rewrite of every tool implementation.

## Universal calls and results

A provider creates a UniversalToolCall containing the tool name, arguments,
transport, optional call/trace/invocation identifiers, user identity and
approval state.

The executor always returns:

    {
      "success": true,
      "data": {},
      "error": null,
      "metadata": {
        "tool": "example",
        "transport": "mcp",
        "executor": "local"
      }
    }

Failures use the same shape with success=false and a non-null error.

## Safety boundary

Input validation happens before execution. Authorization, policy and approval
are explicit hooks on the executor. Tools that require approval are denied
unless the call is already approved or an approval hook returns true.

Registry defaults are deliberately conservative for newly normalized tools:
read_only=false and requires_approval=true. Existing legacy execution through
registry.execute remains backward compatible until the caller migrates to the
UniversalToolExecutor.

## Remote Android execution

A tool may declare:

    "executor": {
      "type": "remote_local_agent",
      "agent_id": "android-bedroom",
      "timeout_seconds": 30
    }

The executor persists a job through the Local Agent Gateway and waits for the
agent result with a bounded timeout. The public gateway implementation is
provided by issue #126; this layer keeps the dependency behind a runtime import
so the universal platform remains independently testable.

## MCP adapter

The ChatGPT Apps SDK / MCP bridge now registers its read-only bridge tools in
the same registry and calls UniversalToolExecutor. The bridge therefore owns
protocol formatting only, not a second tool implementation.

New tools should opt into MCP exposure explicitly with
supported_transports=["mcp"]. This avoids accidentally publishing every
legacy local tool while their metadata is still being audited.

## Migration path

1. Keep existing tool functions unchanged.
2. Add metadata to the registry entry.
3. Migrate callers from registry.execute to UniversalToolExecutor.
4. Add transport support explicitly.
5. Add an agent target for tools that must execute on Android.
6. Correlate the UniversalToolCall with ExecutionTrace in the caller.

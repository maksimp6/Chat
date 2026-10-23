# Universal Tool Platform

Logos uses one provider-agnostic contract between a tool registry and every
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

The headless Issue worker uses the trusted `issue-review-v1` profile. Its
`ReadOnlyToolPolicy` is a Python call argument, separate from model-visible
parameters and issue text. The adapter offers only allowlisted local filesystem
read tools, omits hosted/MCP tools and carries the same policy across every
continuation. The executor denies other tool calls before approval and execution,
including unadvertised tool names and calls claiming approval. The ordinary
interactive client keeps its existing tool profile unless its caller supplies a
scoped policy. An Issue task cannot enable an edit profile through its body.

The worker requires an explicit `ALICE_MEMORY_PATH` outside its checkout before
application initialization, including calls using an injected client. It rejects
an active runtime context, whose private store would otherwise take
precedence over that environment setting. The headless worker stays separate
from runtime-scoped execution. Reads are limited to tracked files of that checkout;
known credential file names, environment
files and key/store suffixes are excluded. The provider's actual root must match
the authorized root. Recursive grep/glob and host system inspection are not offered
by this profile. File reads, AST outline, hashes, file metadata and directory
metadata remain available. These rules do not prove that every tracked source is
free of accidentally committed secrets, and directory metadata is not a secret
redaction service. Native Issue self-repair is disabled by this profile; a future
write stage needs a separate trusted admission rather than an issue-text switch.

`run_command` requires approval outside review mode. Approved child processes get
a minimal explicit environment rather than provider keys or other inherited
application credentials. An approved shell command still has the host process's
filesystem and network privileges; its working directory is not a sandbox.
ToolRegistry chooses the callable signature before invoking it and never retries
an effectful function after an internal `TypeError`.

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

MCP is the complete external tool surface: every tool registered in the
Universal Tool Registry is exposed through MCP. The registry preserves any
existing transport declarations and automatically adds "mcp", so legacy tools
do not need individual adapter implementations or duplicated handlers.

MCP does not bypass the universal safety boundary. The same validation,
authorization, policy and approval checks apply to MCP-originated calls.
Tools marked read-only may execute directly; tools requiring approval remain
blocked until the existing approval flow authorizes the call.

## Migration path

1. Keep existing tool functions unchanged.
2. Add metadata to the registry entry.
3. Migrate callers from registry.execute to UniversalToolExecutor.
4. Keep the tool registered in the Universal Tool Registry; MCP support is
   added automatically.
5. Add an agent target for tools that must execute on Android.
6. Correlate the UniversalToolCall with ExecutionTrace in the caller.

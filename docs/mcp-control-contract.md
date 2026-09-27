# MCP control and runtime-scope contract

Alice Pro exposes MCP over Streamable HTTP at `POST /mcp`. Tool discovery and
execution remain separate: `tools/list` returns public descriptors, while every
`tools/call` crosses `UniversalToolExecutor` for schema, transport, policy, and
approval enforcement. Registry objects, callables, connector configuration,
and credentials are never returned by the MCP control surface.

## Runtime scope extension

An MCP client that addresses preview-runtime resources supplies the following
extension fields in `params._meta`:

| Field | Meaning |
| --- | --- |
| `alice/runtime_id` | Runtime in whose scope the tool call executes. |
| `alice/resource_runtime_id` | Runtime that owns the addressed resource. Optional; defaults to the caller runtime. |

`alice/resource_runtime_id` is invalid without `alice/runtime_id`. The HTTP
adapter propagates the authenticated owner and both runtime identifiers to
`RuntimeDispatcher`. The dispatcher rejects an owner mismatch or a resource
runtime different from the caller runtime before invoking
`UniversalToolExecutor`. A missing runtime connector produces a stable,
credential-free `Runtime connector is unavailable` response.

Calls without `alice/runtime_id` retain the non-preview MCP behavior and still
execute through `UniversalToolExecutor`.

## Audit behavior

Each well-formed `tools/call` creates an invocation and correlated
`ExecutionTrace`. Runtime-scoped calls record the runtime identifier in the
request trace. Cross-runtime denials add an `mcp_runtime_scope_violation` event;
missing connectors add an `mcp_runtime_connector_unavailable` event. Trace and
client errors contain identifiers and policy outcomes only, never bearer
tokens, credentials, registry implementations, or connector configuration.

# Alice Pro MCP control endpoint

Alice Pro exposes its control plane through the existing /mcp endpoint. The Preview deployment already runs Alice Pro in Docker on the current VPS, behind the repository's Traefik routing; no second MCP server is introduced.

## Transport

Use Streamable HTTP at:

    https://<preview-host>/preview/<preview-key>/mcp

The endpoint supports the current 2026 MCP request style through server/discover and also accepts an initialize handshake for compatibility with legacy clients.

The deployment may still provide Mcp-Method and Mcp-Name routing headers. They are optional at the MCP boundary and are validated when present.

## Authentication

Production access must use an authenticated mode:

- OAuth token introspection via ALICE_MCP_INTROSPECTION_URL; or
- a private bearer token via ALICE_MCP_BEARER_TOKEN.

Anonymous access is intended only for local/development use and requires explicit ALICE_MCP_ALLOW_ANONYMOUS=true.

For authenticated users, runtime sessions and invocations are scoped by the stored user_id. Legacy records without trusted ownership are not exposed to authenticated users.

## Control-plane tools

- alice_get_system_status — runtime, MCP and model health.
- alice_list_agents — registered agent descriptors.
- alice_create_session — create an owned runtime session.
- alice_get_session — inspect an owned session.
- alice_create_invocation — create an invocation bound to an existing session and conversation; it does not execute model work by itself.
- alice_get_invocation — inspect invocation lifecycle state.
- alice_get_invocation_trace — retrieve the sanitized persisted Execution Trace.
- alice_cancel_invocation — cancel an owned active invocation.

These tools are deliberately narrower than the internal local/external MCP tool registry.

## Agent workflow

The intended control path is:

GitHub task → GitLab agent workspace → Alice Pro control-plane MCP → ExecutionTrace/status → CI/review

The current MCP surface provides the Alice Pro control-plane part. A GitLab Duo Agent launch adapter remains a separate integration concern and must only be used when a verifiable launch operation is available.

## Preview verification

Every Preview deployment already has a health check at:

    /preview/<preview-key>/healthz

and the existing workflow verifies critical web assets after deployment.

## Security constraints

Do not put API keys, OAuth client secrets, bearer tokens, signing material or other credentials in MCP arguments, issues, prompts, ExecutionTrace or logs.

Protected-branch operations, merges, releases and deployments remain outside this control-plane slice and continue to require explicit confirmation and policy enforcement.

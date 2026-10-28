---
name: mcp-authorization-diagnosis
description: Diagnose MCP initialization, tool discovery and authorization while preserving owner identity and granted scopes.
---

## Purpose
Separate transport, protocol, product service and authorization failures (H06–H08).

## Non-goals
No parallel business logic, production anonymous workaround, new credential entrance or scope bypass.

## Inputs
Exact client/server revision, supported protocol revisions, operation schema, principal/owner resolution, granted scopes and current auth-mode configuration without secret values.

## Tools
Read current MCP service adapters, identity modules and deterministic tests. Use synthetic identities and read-only discovery first.

## Procedure
1. Test endpoint reachability, initialize negotiation, tools/list and tool-call schema independently.
2. Map the operation onto existing conversation/message/file/trace services and persistence.
3. Carry the resolved principal and granted scopes through request context to the operation; never reduce authorization to a Boolean before scope checks.
4. Test cross-owner denial and read-only token denial for control actions.
5. Check mutation annotations and the connected approval path; a flag alone is not an approval flow.
6. Inventory central identity, legacy compatibility and internal bootstrap/admin paths; missing identity or unknown mode fails closed.
7. Verify configured redirects/resources and bounded preview assumptions; do not choose an owner by allowlist order.

## Approval boundaries
Read-only diagnosis does not authorize OAuth reconfiguration, credential changes or consequential control calls. Keep current global authorization policy authoritative.

## Validation
Supported clients negotiate; intended read calls work; wrong owner/scope/redirect cases fail; safe health evidence identifies active modes. Live interoperability is separately demonstrated.

## Failure behavior
Do not label successful HTTP reachability as successful OAuth/tools. Historical PRs #233/#327 are unmerged; #781's merged foundation does not prove all production clients work.

## Output
Stage-by-stage evidence, failing boundary, negative tests and live checks still missing.
Sources: [H06–H08](../../../docs/lessons/README.md), [MCP architecture](../../../docs/mcp/architecture.md).

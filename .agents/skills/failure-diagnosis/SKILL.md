---
name: failure-diagnosis
description: Find the first broken boundary behind a failure, from evidence rather than guesses. Use when an invocation or tool loop fails, a saved setting does not reach the provider request, an MCP server is reachable but its tools or identity fail, CI is red, or a TypeScript mock or deferred promise disagrees with its real response type.
---
## Purpose
Turn a failure into a diagnosis: the failing boundary, the evidence for it, and the smallest fix that would address it. Every mode follows the same method: follow the identifiers, find the first mismatch, and never hide the original failure (H03–H08, H18).

## Non-goals
- Do not create provider-specific bypasses, and do not turn a tool failure into a success.
- Do not treat a saved setting as proof of model behavior, or a reachable endpoint as a working OAuth or tool flow.
- Do not rerun CI blindly, weaken a required check, or rewrite unrelated code.
- Do not fix types with broad casts, `any`, or global replacements.
- Do not put credentials, serials or real prompt content into evidence.

## Inputs
- A failing invocation ID, trace, CI run or job name, or a reproducible request.
- For settings: the expected option, its enabled and disabled values, and the current UI serialization.
- For MCP: the exact client and server revisions, supported protocol versions, the principal and granted scopes (without secret values), and the auth-mode configuration.
- For TypeScript: the compiler diagnostic, the response interface, and the failing mock or call site.

## Tools
- Trace and invocation reads: `GET /api/invocations/<invocation_id>` and `/api/invocations/<invocation_id>/trace` (`runtime/api.py`).
- Code inspection: `static/settings/settings_params.js`, `chat/mcp_routes.py`, `yandex_client_modules/request_mixin.py`, the MCP service adapters and identity modules.
- GitHub workflow, job, step and log reads for CI.
- Runtime validators already in the repository. Focused tests, request-capture tests, typechecks and builds only when the owner asks; otherwise report them as `NOT_RUN`.

## Procedure
1. **Follow the identifiers** `invocation_id`, `session_id`, `conversation_id` and `trace_id`, or the exact commit and failing check names. Read the trace or logs before forming a theory.
2. **Choose the mode** that matches the symptom, then find the first mismatch in its chain.

**Invocation and tool loop**
- Find the first mismatch among request shaping, provider response, tool call, continuation and persistence.
- Verify that every tool call crosses UniversalToolExecutor.
- Check `runtime_id` and resource isolation where runtime resources are involved.
- Confirm errors are recorded in the trace and that no success is fabricated.

**Settings to request**
- Map UI field → serialized value → API contract → persisted value → retrieval → provider field or tool.
- Check each transition separately, by reading code and data.
- Confirm enabled values reach the intended request, disabled values do not, and omitted, default and stale values behave as designed.
- Confirm that a reload keeps the setting and the next request uses that exact value.

**MCP connection and authorization**
- Check reachability, initialize negotiation, `tools/list` and the tool-call schema as separate stages, starting with read-only discovery.
- Map the operation onto the existing conversation, message, file and trace services.
- Carry the resolved principal and granted scopes through the request context. Never reduce authorization to a boolean before the scope check.
- Review cross-owner and read-only denial for control actions. A mutation annotation is not an approval flow.
- Missing identity or an unknown auth mode fails closed.

**Red CI**
- Identify the failing checks on the exact current commit.
- Separate infrastructure or transient failures from deterministic code or test failures.
- Read the smallest relevant job steps and logs, and map the first causal failure to touched files or configuration.
- Check the workflow's event triggers, branch and path filters, job conditions, and the actual status names. A skipped required check is not a passed one.

**TypeScript contract mismatch** (only where the TypeScript app exists)
- Trace the promised value from the API interface to the consumer and the mock.
- Parameterize the deferred promise and its resolver with the actual response type. Remove unnecessary `any`, and check prop compatibility on its own.
- Inspect the exact diff. A script that exits zero is not proof that it matched.

3. **Describe the fix and its regression.** Name the boundary the regression should reproduce. Write or run it only when the owner asks.

## Approval boundaries
Read-only diagnosis needs no new approval. Consequential tool actions, reruns that cost money, reconfiguring OAuth, changing credentials and any live paid request keep their existing authorization. Prefer request-capture mocks with synthetic values. A code change follows the repository's publication rules.

## Validation
- Correlation IDs and check names stay consistent across the chain, and the failing boundary is shown by trace, log or code evidence.
- Each diagnosis names the failing check or boundary, the causal evidence, a reproduction path and the success criterion.
- Positive and negative cases are shown by code reading, or by owner-requested tests. Otherwise they are reported as `NOT_RUN`.
- Live interoperability with a real client is a separate demonstration.

## Failure behavior
- If logs or trace are missing or ambiguous, report the evidence gap instead of inventing a cause.
- Preserve partial output and trace evidence when possible.
- Historical cases are diagnostic examples, not proof that the same defect exists on the current head.

## Output
The failing boundary and mode, the evidence with file and line references, the first mismatch, the fix scope, the regression to add and its status (`NOT_RUN` unless requested), and any unverified provider, live or CI behavior.
Sources: [docs/lessons/README.md](../../../docs/lessons/README.md), [docs/mcp/architecture.md](../../../docs/mcp/architecture.md), [docs/architecture/ai-execution-pipeline.md](../../../docs/architecture/ai-execution-pipeline.md).

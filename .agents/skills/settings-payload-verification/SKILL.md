---
name: settings-payload-verification
description: Diagnose UI settings that persist but do not affect the outgoing provider request.
---

## Purpose
Locate the first broken settings-to-request transition, rather than changing working storage (H04).

## Non-goals
Do not assume saved UI state proves model behavior; do not collapse built-in, local and external MCP tool adapters.

## Inputs
One expected option, its enabled/disabled values, current UI serialization, settings API/storage and provider request builder.

## Tools
Inspect static/settings/settings_params.js, mcp_routes.py and yandex_client_modules/request_mixin.py; use focused deterministic request-capture tests with synthetic values.

## Procedure
1. Map UI field → serialized value → API contract → persisted value → retrieval → provider field/tool.
2. Test each transition separately and find the first mismatch.
3. Verify supported tool categories, selected skills and tool configuration are handled by their appropriate adapters.
4. Assert enabled values enter the intended request and disabled values do not; cover omitted/default/stale settings.
5. Verify a reload preserves the setting and the next request consumes that exact value.
6. Use sanitized projections when inspecting payloads; keep credentials and real prompt content out of evidence.

## Approval boundaries
Prefer request-capture mocks; a live paid request needs the task's existing provider/budget authorization.

## Validation
Both positive and negative payload assertions pass; storage and UI round-trip evidence agree with the actual outgoing schema.

## Failure behavior
If storage passes but payload assembly fails, fix that boundary. Historical H04 is a diagnostic example, not proof the same defect exists on the current head.

## Output
Mapping, first failing boundary, regression evidence and any unverified provider behavior.
Sources: [H04](../../../docs/lessons/README.md), [AI pipeline](../../../docs/architecture/ai-execution-pipeline.md).

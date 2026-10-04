# OWASP ASVS 5.0.0 level 2 baseline

Alice Pro tracks a **selected applicable subset** of OWASP ASVS 5.0.0 Level 1/2 requirements in `asvs-l2.yaml`.

This file is not a claim of full ASVS certification. It is a deterministic project security baseline tied to current repository evidence.

Requirement IDs are always version-qualified, for example `v5.0.0-3.4.4`, because OWASP explicitly warns that identifiers change across major ASVS releases.

## Migration from 4.0.3

The previous draft used ASVS 4.0.3 identifiers. It has been rebuilt against the official OWASP 4.0.3 → 5.0.0 mapping and the ASVS 5.0.0 requirement list rather than mechanically renumbered.

A major-version migration may split, merge, remove or substantially rewrite requirements. Therefore evidence/status was re-evaluated against the **5.0.0 requirement text**, not inherited just because an old number had a mapping.

Current tracked subset: **26 requirements: 9 met, 15 partial, 2 gaps**.

## Current gaps

| Requirement | Gap |
|---|---|
| v5.0.0-2.4.1 | No unified anti-automation/rate-limit control for all costly or abuse-sensitive functions. |
| v5.0.0-3.4.2 | `mcp_server/transport.py` still allows wildcard CORS. |

## Important partials

- **v5.0.0-13.3.1 Secret management.** Cloud.ru Secret Management exists, but #755 still owns migration of remaining credential consumers.
- **v5.0.0-3.4.3 CSP.** CSP is still report-only until remaining inline-style debt is removed.
- **v5.0.0-3.4.1 HSTS.** Enforcement belongs to the Cloud.ru ingress/API Gateway; end-to-end evidence is still required.
- **v5.0.0-16.5.1 Generic errors.** The RFC 9457 migration ratchet still tracks legacy exception-derived responses.
- **v5.0.0-15.1.1 / v5.0.0-15.2.1 Dependencies.** Scanner evidence exists, but remediation-time policy and open findings prevent a `met` status.

## Rules

- `tests/test_asvs_checklist.py` verifies ASVS version 5.0.0, version-qualified IDs, unique IDs, evidence existence, and the gap ratchet.
- A `met` entry must name deterministic repository evidence.
- A `partial`, `gap`, or `n/a` entry must explain why it is not `met`.
- Closing a gap requires the implementation fix, deterministic evidence and lowering `GAP_BASELINE` in the same change.
- A future ASVS major-version bump requires an explicit migration using the official OWASP mapping/current requirement source. Do not silently rewrite IDs.

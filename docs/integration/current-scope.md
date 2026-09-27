# Current-scope integration staging

This branch is the staging surface for the parallel workstreams coordinated by
issue #343 and the architecture in issue #350. It is deliberately **not for
auto-merge**.

## Inclusion order

Candidates are admitted only when their live required checks are green and they
remain compatible with the accumulated branch:

1. #347 — runtime owner policy.
2. #349 — `RuntimeLoader` (after #347).
3. The host-managed preview lifecycle successor to #341.
4. Runtime resource-isolation slices from #350.
5. Independent frontend, MCP, plugin, cloud, agent, and Android slices whose
   dependencies are satisfied.

## Initial gate snapshot (2026-09-26)

No focused pull request is included yet.

| PR | Head | Live gate | Compatibility result | Decision |
| --- | --- | --- | --- | --- |
| #347 | `26b68f31a9a99112a153597976b64e8200748caa` | Blocked: `Application tests` failed; all other non-skipped checks passed. | Applies cleanly to `07fdd0d50a0e099877056f0d2225d1bf47e60d8f`. Its focused gateway suite has four failures because error-path requests return HTTP 404 instead of the expected 502/503/504 responses. | Excluded until its owning branch is green. |
| #349 | `aeb526d77a235829741a12c7b76b9cab3c6eac6a` | Blocked: `Application tests` failed; all other non-skipped checks passed. | Applies cleanly after #347, and its local full suite passes independently, but the required live check is red. | Excluded until #347 is admitted and its own live checks are green. |

A trial merge in the required #347 → #349 order was conflict-free. The combined
backend suite reproduced #347's four gateway failures (705 passed, 6 skipped),
so no integration-only conflict resolution is appropriate. The failure belongs
in #347's focused branch and must be fixed there before reintegration.

## Operating rule

Update this branch from `master` before every admission. Do not restore
per-preview Flask processes, containers, `localhost` transport, or
`runtime_port`. Preserve the caller `runtime_id`, and route shared-resource
access through `RuntimeDispatcher` as required by #350.

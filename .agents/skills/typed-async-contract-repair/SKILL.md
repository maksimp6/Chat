---
name: typed-async-contract-repair
description: Repair deferred promises and asynchronous mocks when their TypeScript types disagree with the real response contract.
---

## Purpose
Fix the actual type boundary while retaining runtime behavior (history H03).

## Non-goals
Do not silence errors with broad casts, blanket any or unrelated runtime rewrites. Do not assume the historical TypeScript app is today's Logos UI.

## Inputs
Compiler diagnostic, response interface, failing mock/call site, component props, package scripts and current revision.

## Tools
Repository search, TypeScript compiler, focused tests and the project's production build.

## Procedure
1. Trace the promised value from API interface to consumer and mock.
2. Parameterize the deferred Promise and resolver with the actual response type.
3. Remove unnecessary any and check prop compatibility independently.
4. Inspect the exact diff; do not use blind global replacements.
5. Run the typecheck, affected asynchronous tests and production build declared by this project.

## Approval boundaries
Local code/test work follows the authorized task; no deployment is implied by a build.

## Validation
The original compiler failure is gone, promises resolve the intended response, tests preserve ordering/error behavior and the build succeeds on the changed head.

## Failure behavior
A replacement script exiting zero is not proof it matched. Missing dependencies or unavailable original sources are explicit blockers, not successful builds.

## Output
Broken contract, focused diff, commands/results and unchanged runtime expectations.
Source: [H03 and historical-build limitation](../../../docs/lessons/README.md).

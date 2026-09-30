---
name: alice-runtime-debugging
description: Diagnose Alice Pro invocation, tool-loop, runtime isolation and Execution Trace failures.
---
## Purpose
Find failures across InvocationContext, provider calls, UniversalToolExecutor and Execution Trace without hiding errors.

## Non-goals
Do not create provider-specific bypasses or convert tool failures into successful responses.

## Inputs
- Failing invocation/trace or reproducible request.
- Relevant runtime/provider/tool configuration.

## Tools
Use trace/invocation reads, focused tests, runtime validators and repository code inspection.

## Procedure
1. Follow invocation_id, session_id, conversation_id and trace_id across the request.
2. Locate the first mismatch among request shaping, provider response, tool call, continuation and persistence.
3. Verify every tool call crosses UniversalToolExecutor.
4. Check runtime_id/resource isolation where runtime resources are involved.
5. Confirm errors are recorded in trace and user-visible success is not fabricated.
6. Add a focused regression reproducing the broken boundary.

## Approval boundaries
Read-only diagnosis needs no new approval; consequential tool actions keep their existing approval rules.

## Validation
Correlation IDs remain consistent, the failing boundary is reproducible, and the regression test proves the correction.

## Failure behavior
Preserve partial output and trace evidence when possible; never swallow the original failure.

## Output
Return failing boundary, correlation evidence, fix scope and regression test.

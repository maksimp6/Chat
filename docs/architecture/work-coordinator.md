# Work Coordinator runtime contract

Issue #580 implements one shared preparation layer for all specialist roles.

The coordinator is deliberately narrower than Team Lead:

- Team Lead decides who owns the work.
- Work Coordinator prepares the smallest current context for that owner.
- The specialist performs implementation/analysis.
- Verification and solution review remain independent lifecycle stages.
- Release Manager/maintainer controls merge readiness.
- Operations Observer and Process Governor handle non-convergence/process gaps.

## Composition

The coordinator composes existing project contracts rather than replacing them:

```text
AgentTaskPlan
    +
TaskPacket
    +
HybridRetriever
    +
AgentTaskEvidence
    +
ExecutionTrace
    |
    v
CoordinatorHandoff
```

Actual agent selection and tool invocation remain under
`ConversationAgentRouter -> RuntimeDispatcher`.

## Authority

Work Coordinator is a coordination-only role.

It cannot:

- be selected as an implementation owner;
- perform solution review;
- merge;
- bypass approvals;
- change production code while acting as coordinator;
- settle an architecture dispute;
- silently escalate to a stronger reasoning tier.

Its custom-agent profile is read/search only.

## Expensive reasoning gate

`assert_reasoning_allowed()` applies two deterministic checks:

1. requested tier cannot exceed the TaskPacket budget tier;
2. a repeated strong call with the same evidence fingerprint is rejected.

No process-global retry counter is required.

## User-facing preparation

The user handoff intentionally omits internal context/evidence bodies and exposes only:

- objective;
- canonical task state;
- next meaningful step;
- latest event/blocker supplied by the caller;
- compact budget/usage/billing summary.

The richer progress chart remains #581 and must use real telemetry rather than guessed
percentages.

# Work Coordinator

The Work Coordinator is Alice Pro's shared preparation layer. It reduces repeated
repository/model work without becoming another implementation owner.

## Position in the office

```text
User / Team Lead
       |
       v
Work Coordinator
  cache -> memory -> hybrid retrieval
       |
       v
selected specialist
       |
       v
verification / solution review / maintainer
```

Team Lead remains responsible for selecting the owner role. The dispatch model remains
authoritative for lifecycle stage, role, backend and capabilities.

The coordinator never gains implementation or merge authority from having more context.

## Inputs

The coordinator consumes existing contracts:

- `AgentTaskPlan` from the role/stage/backend dispatch model;
- `TaskPacket` from the GitHub-first context layer;
- `HybridRetriever` for cache/shared-memory/AST evidence;
- `AgentTaskEvidence` for the canonical task state;
- `ExecutionTrace` for billing/usage and trace correlation.

It does not create another agent registry, task state machine, tool executor, model
provider abstraction or database.

## Recipient-specific handoffs

One immutable handoff contract is shaped for five audiences.

### Specialist

Receives the objective, expected deliverable, known facts, open questions,
do-not-repeat attempts, changed files, evidence references and bounded retrieved
context.

### Maintainer

Receives exact-head provenance, changed files, evidence/context references, open
questions, blocker and usage summary. It does not receive a dump of retrieved context.

### Operations Observer

Receives task state, usage/retrieval counts, context references, blocker and
do-not-repeat evidence for non-convergence detection.

### Process Governor

Receives process-relevant evidence refs, open questions, do-not-repeat history,
escalation target, blocker and usage. The coordinator does not decide the process fix.

### User

Receives only the task objective/state, next meaningful step, latest meaningful event,
blocker and usage/budget summary. Internal evidence bodies and raw prompts are omitted.

## Soft-skill context

The handoff carries explicit task communication metadata:

- urgency;
- optional confidence supplied by the caller;
- latest meaningful event;
- blocker.

These are facts supplied to the coordinator, not inferred emotions or hidden model
state. The coordinator must not manufacture reassurance.

## Reasoning budget

Supported tiers are `cheap`, `normal` and `strong`.

A task's `budget_tier` is a hard ceiling. A requested tier above the ceiling fails
closed.

For strong reasoning:

```text
no previous strong call -> allowed
new evidence fingerprint -> allowed
same evidence fingerprint -> rejected
```

This implements the repository rule:

> No new evidence -> no new expensive reasoning.

The coordinator keeps no process-global usage counter. The caller supplies the evidence
fingerprint of the previous strong call when one exists.

## Telemetry

The coordinator writes two bounded trace surfaces:

- the existing `hybrid_retrieval` / `retrieval_operations` metadata;
- `coordinator_handoff_prepared` / `coordinator_handoffs`.

Coordinator trace records contain role/stage/state, exact-head provenance, cache and
retrieval status, reasoning tier and saved source/token counts. They do not copy raw
handoff content, prompts or retrieved evidence bodies.

Billing remains owned by `ExecutionTrace`; the handoff only surfaces its compact
summary.

## Runtime boundary

Actual conversation-agent selection, handoff and tool execution continue through
`ConversationAgentRouter` and `RuntimeDispatcher`. The coordinator prepares context
but does not bypass runtime/conversation isolation.

# Process observation and governance

Alice Pro separates normal engineering coordination from cross-agent observation and
process improvement.

The goal is not to create a second manager for every task. Most work should still flow
through Team Lead and the existing specialist roles. Operations Observer and Process
Governor are used only when the normal loop stops converging.

## Roles

### Operations Observer

Operations Observer is read-mostly. It watches issues, pull requests, review threads,
CI history, and delegation history. It does not choose the disputed technical solution
and does not edit production code.

The observer emits a structured escalation when deterministic evidence shows that the
normal role/maintainer loop is stuck.

### Process Governor

Process Governor consumes evidence-backed escalations about the engineering process.
It asks whether the failure came from the process itself rather than from one isolated
implementation mistake.

Supported classifications:

- `ownership_gap` — responsibility between roles is unclear or overlapping;
- `missing_skill` — the organization lacks a reusable procedure for recurring work;
- `ambiguous_policy` — existing instructions permit conflicting interpretations;
- `validation_gap` — CI/tests/checks cannot settle the disagreement;
- `tooling_gap` — agents lack deterministic data or automation needed to decide;
- `permission_gap` — required action has no safe authorized path;
- `retry_loop` — repeated retries consume time/tokens without adding evidence.

Governor proposes the smallest process correction supported by the escalation. It does
not take over the disputed production implementation.

## Deterministic escalation signals

The initial observer policy is intentionally simple and cheap. Escalate when one of
these conditions is supported by repository evidence:

1. **Delegation cycle** — work is delegated A -> B -> A without new evidence that
   changes the responsibility boundary.
2. **Unresolved contradiction** — two roles keep incompatible root-cause or solution
   claims after two evidence-bearing exchanges.
3. **Repeated failed fix** — three implementation attempts fail with the same failure
   class or unchanged blocker.
4. **Review ping-pong** — the same pull request is returned to implementation more than
   twice for the same class of finding.
5. **Stalled blocker** — a blocker remains with no material progress across two
   maintainer passes.
6. **Replacement churn** — more than two replacement pull requests are opened for the
   same unchanged objective.
7. **Maintainer stall** — a PR receives a `@claude` maintainer handoff but has no
   merge, `BLOCKED:` / changes-requested status, or `DEFERRED:` status by the
   second scheduled hourly Observer pass. Reactions and ordinary progress comments are
   acknowledgements, not completion.

These signals trigger investigation. They do not prove fault, incompetence, or that a
specific role should be replaced.

## Escalation record

Use a compact record so a stronger model is not forced to reread the whole discussion.

Required fields:

```text
scope: issue or PR
participants: roles/agents involved
trigger: deterministic signal
rounds_or_attempts: integer/count summary
evidence:
  - exact CI status/run or review/delegation fact
current_blocker: short description
target: team-lead | maintainer | process-governor | owner
```

Optional fields may include a suspected process classification and links to earlier
related escalations.

Never include secret values, raw credentials, private keys, access tokens, or raw
sensitive payloads in an escalation record.

## Escalation routing

Use the lowest level that can safely resolve the problem:

```text
specialist disagreement
        |
        v
Team Lead / maintainer
        |
        v
Operations Observer detects repeated non-convergence
        |
        v
Process Governor
        |
        v
process issue / protected PR
        |
        v
owner only when an approval boundary is crossed
```

A one-off technical failure normally goes to Team Lead or the relevant specialist.
Process Governor is for repeated or structural failures, not ordinary bug triage.

### Maintainer handoff state

The hourly Observer tracks `@claude` mentions on pull requests as maintainer
dispatches. Completion is deliberately narrow: the PR merges, Claude posts an explicit
`BLOCKED:` or `DEFERRED:` status, or Claude submits a changes-requested review.
Other comments, commits, labels, and reactions do not suppress the stall.

Passes are counted against the Observer's scheduled minute (:17), not raw elapsed
hours. This makes a handoff just after :17 escalate on the next two scheduled
boundaries (:17 of the next two hours), rather than accidentally waiting for a third
pass.

This is intentionally deterministic and does not call another model merely to discover
that nothing changed. The finding is evidence for Process Governor to inspect dispatch
or workflow policy; it never weakens merge protection.

## Process-change contract

A Process Governor change must:

1. link to the escalation evidence;
2. identify the process hypothesis;
3. change the smallest relevant process surface;
4. define a measurable signal for whether the correction helped;
5. use a normal branch and protected pull request;
6. receive normal CI/review;
7. never be self-approved or self-merged by the governor.

Allowed process surfaces include agent role instructions, development policy/docs,
reusable agent skills, and non-privileged workflow logic.

The owner's explicit approval remains required for production deployment, destructive
database changes, secrets, CODEOWNERS, branch protection, repository permissions, and
any expansion of agent authority.

## Cost policy

Observation should be deterministic first. Counts, state transitions, CI conclusions,
review rounds, and delegation cycles should be collected without a strong model where
possible.

Use a lightweight role model to classify an escalation. Escalate to the strong
architecture/reasoning path only when the process diagnosis is genuinely ambiguous or
multi-domain.

## Relationship to skill-first runtime

This document defines the process before reusable observer/governor skills exist.

After #566 lands, recurring observer and governor procedures should move into reusable
skills and the role profiles should become thinner. Until then, do not duplicate or
modify the open skill-runtime implementation just to support this governance slice.

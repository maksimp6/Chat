# Sprint workflow

Logos uses short, reviewable development iterations. The goal is a verified vertical increment, not a pile of closed checkboxes.

## Cadence

Use a one-week default sprint. A smaller 2–3 day sprint is appropriate for a focused fix; a longer cycle should have an explicit reason.

Each sprint follows:

1. Planning: choose a Sprint Goal and a small set of issues that directly contribute to it.
2. Development: implement vertical slices, keeping work independently testable and mergeable.
3. Demo / verification: record acceptance evidence, separately authorized check results, user-visible behavior and operational impact.
4. Retrospective: record one or two process changes for the next sprint.

Urgent production bugs use a separate interrupt lane and do not silently change the Sprint Goal.

## Sprint Goal

Every sprint has one sentence describing the outcome.

Good: “Trace Viewer can navigate between related API request, polling, and response events.”

Bad: “Close issues #58, #63, #80.”

Issues are selected because they contribute to the goal, not because the goal was invented after the work.

## Definition of Ready

An issue is ready when it has:

- a concrete problem or outcome;
- acceptance criteria that can be checked;
- known dependencies and blockers;
- an identified validation method;
- a small enough scope for one reviewable increment, or an explicit decomposition plan.

Missing product details should be surfaced as questions or explicit defaults. Do not silently invent requirements.

## Definition of Done

A change is done when:

- implementation is complete for the agreed slice;
- relevant documentation and acceptance evidence are updated;
- validation follows the owner's request and [AGENTS.md](../../AGENTS.md);
- code tests run only when separately requested inside GitHub Actions runner;
  unrun checks are recorded as `NOT_RUN`;
- the actual diff is reviewed and delivered by the authorized route;
- the delivered revision and requested visible result are checked where authorized;
- security, secrets, tracing, billing, and migration impacts have been checked where relevant.

Record evidence and remaining blockers before closing work. Owner acceptance and
merge permission are separate decisions; a sprint does not authorize promotion
to master or create an automatic test requirement.

## WIP and prioritization

Default WIP limit: 2 active implementation branches per developer.

Prefer finishing an in-flight slice over starting another. Blocked work is explicitly marked blocked and does not count as silently progressing.

Priority order:

1. production incidents and security issues;
2. blockers for the current Sprint Goal;
3. small enabling work needed by the next vertical slice;
4. independent improvements and dependency maintenance.

## Backlog refinement

Refine the next sprint's candidates before planning:

- clarify acceptance criteria;
- split oversized issues into vertical slices;
- identify dependencies and blockers;
- remove obsolete or duplicate work;
- assign a validation path.

Do not estimate work merely for the sake of producing a number.

## Metrics

Use lightweight team signals, not individual scorecards:

- Lead time: issue start to merged change.
- Cycle time: first implementation commit to merge.
- PR size: changed lines/files and review complexity.
- Validation return rate: changes requiring correction after an authorized check.
- WIP: active branches/PRs not yet done.

Metrics are used to improve the process, not rank people.

## Blockers and changing goals

When work is blocked:

1. record the blocker in the issue or PR;
2. identify whether another independent slice can proceed;
3. avoid leaving a half-finished branch hidden in WIP;
4. return the blocked item to the next planning cycle if necessary.

If the Sprint Goal must change, record the change explicitly in the sprint issue/project item and explain what caused it.

## GitHub mapping

Use GitHub as the source of workflow state:

Issue → authorized delivery route → review → recorded evidence → owner acceptance

Use GitHub Projects for the sprint-level view, Issues for acceptance criteria and dependencies, PRs for implementation/review, and Actions for deterministic validation.

## Sprint template

Create one sprint tracking issue/project item with:

- Sprint Goal;
- selected issue IDs;
- blockers;
- demo/verification notes;
- retrospective notes;
- changes to the Sprint Goal.

Keep the sprint record short enough to read in a minute.

# Architecture Decision Records

Status: current  
Owner: Architecture / Docs Engineer

Use an ADR when a decision must remain understandable after the original issue/PR discussion becomes hard to discover.

## Naming

`NNNN-short-kebab-title.md`, monotonically increasing.

## Required template

```markdown
# ADR NNNN: Title

Status: proposed | accepted | superseded | rejected
Date: YYYY-MM-DD
Owners: ...
Related: #issue, #PR

## Context
What constraint or problem requires a durable decision?

## Decision
What is decided?

## Consequences
What becomes easier, harder, required or forbidden?

## Alternatives considered
What credible alternatives were rejected and why?

## Verification
How can code/config/tests prove the decision is implemented?

## Supersession
Which ADR replaces this one, if any?
```

Issues own implementation work. ADRs record the durable decision; they are not implementation trackers.

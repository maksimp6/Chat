# Logos Agent Skills

This directory is the canonical repository skill tree.

For trigger-based navigation, reusable engineering lessons and their source/coverage
limits, see [the project lessons index](../../docs/lessons/README.md).
The additional documentation packages do not change role allowlists or permissions;
their role-scoped runtime integration is a separate change.

A **role** owns responsibility. A **skill** defines a reusable procedure. A
**tool/MCP capability** performs an action. A **provider/model** is the replaceable
reasoning backend. Execution Trace records which skills were selected.

## Package contract

Each package is `<skill-name>/SKILL.md`. The directory name and frontmatter
`name` must match kebab-case.

Required frontmatter:

```yaml
---
name: example-skill
description: Clear trigger-oriented description.
---
```

Required sections:

- Purpose
- Non-goals
- Inputs
- Tools
- Procedure
- Approval boundaries
- Validation
- Failure behavior
- Output

Keep procedure text provider-neutral. Reference repository scripts instead of copying
large shell recipes. A skill never weakens authentication, authorization, approval,
branch protection or production policy.

Logos exposes metadata from the catalog first and loads the full body only for skills
selected for one invocation. Do not automatically install remote/unreviewed skills.

## Repository rules

Skills follow the repository's `AGENTS.md`. Where it forbids an action, a skill
must not instruct it: no branches or pull requests for code changes (push directly
to `develop`), and no writing or running of tests, typechecks or builds unless the
owner explicitly asks. A skipped check is reported as `NOT_RUN`, never as passed.

## Consolidated skills

Related procedures share one skill. A mode named in its description selects the procedure.

- `3d-printing`: preflight of a single Bambu A1 mini `.gcode.3mf`, read-only
  print watch, and staged delivery evidence.
- `failure-diagnosis`: first broken boundary behind a failure. Modes: invocation
  and tool loop, settings that do not reach the provider request, MCP connection
  and authorization, red CI, and TypeScript contract mismatch.
- `change-delivery`: implement one issue and push it to `develop`, release and
  Cloud.ru readiness on an exact commit, and read-only readiness of an existing PR.
- `evidence-review`: documentation sync, privacy and compliance reconciliation,
  durable-storage proof, security review of a change, and hardware evidence
  bound to its archived verification.
- `frontend-capability-audit` and `service-hypothesis-validation`: single-purpose.

These roles expose procedures only. They do not create a missing slicer,
root-owned service, printer authorization or access to another owner's
files. See `docs/mcp/printing3d-tools.md`.

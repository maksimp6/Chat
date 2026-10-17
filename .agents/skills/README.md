# Alice Pro Agent Skills

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

Alice exposes metadata from the catalog first and loads the full body only for skills
selected for one invocation. Do not automatically install remote/unreviewed skills.

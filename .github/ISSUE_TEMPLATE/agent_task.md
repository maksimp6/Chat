---
name: Agent task
about: A focused task for an Alice Pro role agent or coding-agent backend
title: "[Agent] "
labels: "agent-task"
---

## Goal

One or two sentences: what should change and why.

## Role

Choose the narrowest matching role in the GitHub Agents UI:

- [ ] Team Lead
- [ ] Backend Engineer
- [ ] Frontend Engineer
- [ ] Android Engineer
- [ ] Test Engineer
- [ ] Infra Engineer
- [ ] Security Reviewer
- [ ] Docs Engineer
- [ ] Release Manager

Use a provider agent directly only when the role needs escalation:
Claude Partner Agent (Sonnet) for architecture-heavy/multi-domain work,
Codex for focused test/code work, or `@claude-lite` for bounded cheap maintenance.

## Acceptance criteria

- [ ]
- [ ]

## Files / areas

Where the change most likely belongs (paths, modules, endpoints).

## Checks to run

- [ ] `python -m compileall -q .`
- [ ] `python tests/validate_runtime_modules.py`
- [ ] `pytest -q` (or the focused test files)

## Out of scope

What the agent must not change.

## Dispatch

Assign exactly one primary role/agent. The agent opens a pull request; the maintainer
(Claude, see `AGENTS.md`) merges it only after the repository merge gates are satisfied.

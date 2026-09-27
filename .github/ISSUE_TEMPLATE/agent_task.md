---
name: Agent task
about: A focused task for a coding agent (@claude, @codex, @copilot or @alice)
title: "[Agent] "
labels: "agent-task"
---

## Goal

One or two sentences: what should change and why.

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

Mention exactly one agent in a comment, e.g. `@alice please take this`.
The agent opens a pull request; a maintainer merges it after green CI.

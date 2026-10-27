---
name: Agent task
about: A focused task for a Logos role agent or coding-agent backend
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

Use the repository role and its approved deterministic tools. External model-backed agents are optional escalation paths, not required executors.

## Acceptance criteria

- [ ]
- [ ]

## Files / areas

Where the change most likely belongs (paths, modules, endpoints).

## Checks to run

- [ ] Only explicitly requested real Redmi 9 checks, inside GitHub Actions.
- [ ] Record the actual device/runtime revision, result and evidence; use NOT_RUN when no check was requested.

## Out of scope

What the agent must not change.

## Dispatch

Assign exactly one primary role/agent. The agent opens a pull request; the maintainer
merges it only after the repository merge gates are satisfied.

# `agent_skills`

Repository Agent Skills: discovery, policy and lazy loading.

## Role in a dialog

Where this unit sits in the [life of a dialog](../docs/architecture/modules.md#life-of-a-dialog):

- **Send a message (the turn)**: `POST /api/chat`

## Public API

`from agent_skills import …`: `Skill`, `SkillFormatError`, `SkillNotFoundError`, `SkillPolicyError`, `SkillRegistry`, `SkillRegistryError`, `compose_skill_instructions`.

| Module | Purpose |
| --- | --- |
| [`agent_skills.registry`](registry.py) | Repository Agent Skill discovery, policy and lazy loading. |

Other units reach it only through their own ports:

| Provides | To |
| --- | --- |
| `agent_skills.registry.SkillRegistry` | `chat` |
| `agent_skills.registry.SkillRegistryError` | `chat` |
| `agent_skills.registry.compose_skill_instructions` | `chat` |

## Ports

None. The unit imports only the standard library, third-party packages and its own modules.

## Install

- Nothing to wire: import the modules you need.

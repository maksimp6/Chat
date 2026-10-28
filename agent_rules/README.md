# `agent_rules`

A proposed provider-independent Agent Rules Registry and its candidate enforcement controller. It is not installed in production.

## Public API

`from agent_rules import …`: `controller`.

| Module | Purpose |
| --- | --- |
| [`agent_rules.controller`](controller.py) | Candidate enforcement boundary; not installed in production by this PR. |

## Ports

None. The unit imports only the standard library, third-party packages and its own modules.

## Install

- Nothing to wire: import the modules you need.

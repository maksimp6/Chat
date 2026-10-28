# `agent_office`

Tools that watch the GitHub "agent office" (Claude, Codex, Copilot and Logos): an observer that builds a readable thread for every task, per-agent KPIs, owner labels, and the dispatch model and state machine of dispatched tasks.

## Public API

`from agent_office import …`: `observer`.

| Module | Purpose |
| --- | --- |
| [`agent_office.observer`](observer.py) | Watch the GitHub agent office and build a readable thread for every task. |

Command line:

- `python -m agent_office.observer`

## Ports

None. The unit imports only the standard library, third-party packages and its own modules.

## Install

- Nothing to wire: import the modules you need.

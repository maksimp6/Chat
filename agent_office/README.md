# `agent_office`

Tools for monitoring the GitHub agent office. The observer builds a readable thread for each task and provides per-agent KPIs, owner labels, and the dispatch model and state machine for dispatched tasks.

## Public API

`from agent_office import …`: `observer`.

| Module | Purpose |
| --- | --- |
| [`agent_office.observer`](observer.py) | Monitors the GitHub agent office and builds a readable thread for each task. |

Command line:

- `python -m agent_office.observer`

## Ports

None. The unit imports only the standard library, third-party packages and its own modules.

## Install

- Nothing to wire: import the modules you need.

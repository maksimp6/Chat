# `agent_shell`

A minimal shell for running agent tasks: a durable task store on Kristall, a runner over a handler registry, built-in task kinds and a command line.

## Public API

`from agent_shell import …`: `handlers`, `runner`, `store`.

| Module | Purpose |
| --- | --- |
| [`agent_shell.handlers`](handlers.py) | Built-in task kinds. Each handler is read-only unless its docstring says otherwise. |
| [`agent_shell.runner`](runner.py) | Runs one queued task through a handler registry. |
| [`agent_shell.store`](store.py) | Typed durable task repository using Logos's Kristall client. |

Command line:

- `python -m agent_shell`

## Ports

Everything `agent_shell` uses from other units is a field of `Host` in [`agent_shell/host.py`](host.py), read only when a function runs. Functions keep their names as call shims; other ports are `bound.<name>`.

| Port | Provided by |
| --- | --- |
| `DEFAULT_MODEL` | `agent_runtime.issue_runner.DEFAULT_MODEL` |
| `KristallError` | `kristall.KristallError` |
| `get_memory_client` | `agent_memory.client.get_memory_client` |
| `run_issue_task` | `agent_runtime.issue_runner.run_issue_task` |

## Install

- `composition/wiring.py` registers the `agent_shell` host factory (`register_all()`, or `register_units("agent_shell")` for this unit alone); the host is built on first use.

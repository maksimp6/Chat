# `keel`

Keel Platform: configuration management and reconciliation. It validates the desired state, observes reality through providers (Cloud.ru, DNS, storage), plans and applies actions and recovers from failures.

## Public API

`from keel import …`: `config`, `health`, `planner`, `providers`, `reconciler`, `recovery`.

| Module | Purpose |
| --- | --- |
| [`keel.config`](config.py) | Config loading and validation for Keel Platform. |
| [`keel.health`](health.py) | Health checks derived from config. |
| [`keel.planner`](planner.py) | Planner: generate actions from desired vs observed state. |
| [`keel.providers`](providers/__init__.py) | Providers for Keel Platform - read observed state from cloud/infrastructure. |
| [`keel.reconciler`](reconciler.py) | Reconciler: apply actions to bring reality to desired state. |
| [`keel.recovery`](recovery.py) | Recovery: automated recovery from failures. |

Command line:

- `python -m keel`

## Ports

None. The unit imports only the standard library, third-party packages and its own modules.

## Install

- Nothing to wire: import the modules you need.

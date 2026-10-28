# `observability`

Observability: the Execution Trace (the primary per-invocation record of agent steps, API calls, tools, errors and timing) and the small process-level system log for start-up and failures that happen before a trace exists.

## Public API

`from observability import …`: `system_log`, `trace_manager`.

| Module | Purpose |
| --- | --- |
| [`observability.system_log`](system_log.py) | Centralized process-level system log. |
| [`observability.trace_manager`](trace_manager.py) | Centralized execution trace for the Logos chat pipeline. |

Other units reach it only through their own ports:

| Provides | To |
| --- | --- |
| `observability.trace_manager.ExecutionTrace` | `chat`, `invocation`, `remote_runtime`, `yandex_client_modules` |
| `observability.trace_manager.bind_current_trace` | `chat`, `mcp_server` |
| `observability.trace_manager.get_current_trace` | `chat`, `cloud`, `economy`, `provider_accounts`, `remote_runtime`, `secret_store` |
| `observability.trace_manager.reset_current_trace` | `chat`, `mcp_server` |
| `observability.trace_manager.traced_operation` | `chat`, `provider_accounts` |

## Ports

Everything `observability` uses from other units is a field of `Host` in [`observability/host.py`](host.py), read only when a function runs. Functions keep their names as call shims; other ports are `bound.<name>`.

| Port | Provided by |
| --- | --- |
| `aggregate_billing` | `economy.billing.aggregate_billing` |
| `build_ai_billing_item` | `economy.billing.build_ai_billing_item` |

## Install

- `composition/wiring.py` registers the `observability` host factory (`register_all()`, or `register_units("observability")` for this unit alone); the host is built on first use.

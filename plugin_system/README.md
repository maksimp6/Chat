# `plugin_system`

Plugins: manifest and lifecycle management, plugin access to the existing execution boundaries, and the HTTP API for discovery and lifecycle.

## Public API

`from plugin_system import …`: `routes`.

| Module | Purpose |
| --- | --- |
| [`plugin_system.routes`](routes.py) | HTTP API for shared local plugin discovery and lifecycle management. |

## Ports

Everything `plugin_system` uses from other units is a field of `Host` in [`plugin_system/host.py`](host.py), read only when a function runs. Functions keep their names as call shims; other ports are `bound.<name>`.

| Port | Provided by |
| --- | --- |
| `UniversalToolCall` | `tool_providers.universal_tool_platform.UniversalToolCall` |

## Install

- `composition/wiring.py` registers the `plugin_system` host factory (`register_all()`, or `register_units("plugin_system")` for this unit alone); the host is built on first use.
- `composition/web.py` registers the blueprint `plugin_bp` (`plugin_system.routes`).

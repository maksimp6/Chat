# `model_editor`

The Logos Pro local 3D scene editor: bounded, deterministic primitive geometry, immutable owner-scoped scene versions with STL artifacts, and tools that edit scenes.

## Public API

`from model_editor import …`: `tools`.

| Module | Purpose |
| --- | --- |
| [`model_editor.tools`](tools.py) | Logos Pro universal local tools for editable 3D primitive scenes. |

## Ports

Everything `model_editor` uses from other units is a field of `Host` in [`model_editor/host.py`](host.py), read only when a function runs. Functions keep their names as call shims; other ports are `bound.<name>`.

| Port | Provided by |
| --- | --- |
| `StorageObjectNotFound` | `storage.StorageObjectNotFound` |
| `get_current_owner_id` | `economy.treasury_identity.get_current_owner_id` |
| `storage_provider_from_env` | `storage.contracts.storage_provider_from_env` |

## Install

- `composition/wiring.py` registers the `model_editor` host factory (`register_all()`, or `register_units("model_editor")` for this unit alone); the host is built on first use.
- `composition/tool_sources.py` adds the `3d` tool category from `model_editor.tools`.

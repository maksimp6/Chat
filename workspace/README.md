# `workspace`

The workspace HTTP API: Files and Vector Stores, and a bounded project tree.

## Role in a dialog

Where this unit sits in the [life of a dialog](../docs/architecture/modules.md#life-of-a-dialog):

- **Tools during the turn**: `POST /api/chat`, `GET /api/mcp-servers`

## Public API

`from workspace import …`: `file_routes`, `project_tree`.

| Module | Purpose |
| --- | --- |
| [`workspace.file_routes`](file_routes.py) | Flask routes for Files and Vector Stores API |
| [`workspace.project_tree`](project_tree.py) | Functional project tree API with safe, bounded filesystem traversal. |

## Ports

Everything `workspace` uses from other units is a field of `Host` in [`workspace/host.py`](host.py), read only when a function runs. Functions keep their names as call shims; other ports are `bound.<name>`.

| Port | Provided by |
| --- | --- |
| `Config` | `core.config.Config` |
| `YandexClientError` | `yandex_client_modules.errors.YandexClientError` |
| `YandexResponsesClient` | `yandex_client_modules.client.YandexResponsesClient` |

## Install

- `composition/wiring.py` registers the `workspace` host factory (`register_all()`, or `register_units("workspace")` for this unit alone); the host is built on first use.
- `composition/web.py` registers the blueprints `file_bp` (`workspace.file_routes`), `project_tree_bp` (`workspace.project_tree`).

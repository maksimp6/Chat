# `web_shell`

The web shell: the page, health, readiness and version endpoints, the Cache-Control and unexpected-error policy, and storage start-up.

## Public API

`from web_shell import …`: `install`.

| Module | Purpose |
| --- | --- |
| [`web_shell.shell`](shell.py) | The web shell: page, health, readiness, version, caching, errors and storage start-up. |
| [`web_shell.version`](version.py) | What this process serves: the UI fingerprint, the code revision and the boot time. |

## Ports

Everything `web_shell` uses from other units is a field of `Host` in [`web_shell/host.py`](host.py), read only when a function runs. Functions keep their names as call shims; other ports are `bound.<name>`.

| Port | Provided by |
| --- | --- |
| `KristallError` | `kristall.KristallError` |
| `current_runtime_base_path` | `runtime.current_runtime_base_path` |
| `get_current_owner_id` | `economy.treasury_identity.get_current_owner_id` |
| `get_donation_url` | `core.config.get_donation_url` |
| `get_store` | `storage.records.get_store` |
| `memory_file_path` | `storage.records.memory_file_path` |
| `problem` | `invocation.problems.problem` |
| `storage_initializers` | `composition.web.STORAGE_INITIALIZERS` |

## Install

- `composition/wiring.py` registers the `web_shell` host factory (`register_all()`, or `register_units("web_shell")` for this unit alone); the host is built on first use.
- `composition/web.py` calls `web_shell.install(app)`.

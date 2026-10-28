# `rdc_connection`

Remote Desktop Commander pairing: the `/rdc` redirect to the verification URL of an existing device flow, and task-local limits for authorized RDC callbacks.

## Public API

`from rdc_connection import …`: `pairing`.

| Module | Purpose |
| --- | --- |
| [`rdc_connection.pairing`](pairing.py) | Redirect for the verification URL of an existing RDC device flow. |

## Ports

None. The unit imports only the standard library, third-party packages and its own modules.

## Install

- `composition/web.py` calls `rdc_connection.pairing.install_rdc_pairing(app)`.

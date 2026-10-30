# `api`

API contracts: strict contracts shared by routes, offline validation against the pinned Yandex documentation snapshot, reviewed outbound request profiles and the single outbound admission boundary for Yandex API callers.

## Role in a dialog

Where this unit sits in the [life of a dialog](../docs/architecture/modules.md#life-of-a-dialog):

- **Trace, cost and treasury of a reply**: `GET /api/invocations/<invocation_id>/trace`, `GET /api/treasury/account`

## Public API

`from api import …`: `contracts`, `yandex_transport`.

| Module | Purpose |
| --- | --- |
| [`api.contracts`](contracts.py) | Strict API contracts shared by routes and tests. |
| [`api.yandex_transport`](yandex_transport.py) | Single outbound admission boundary for dedicated Yandex API callers. |

Other units reach it only through their own ports:

| Provides | To |
| --- | --- |
| `api.contracts.ContractViolation` | `runtime` |
| `api.contracts.validate_api_contract` | `chat`, `runtime` |
| `api.yandex_transport.yandex_request` | `tools.narration` |

## Ports

None. The unit imports only the standard library, third-party packages and its own modules.

## Install

- Nothing to wire: import the modules you need.

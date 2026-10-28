# `cloud`

The provider-neutral cloud surface (resource models, policy, provider registry, sandbox environment contract and cloud tools), the Cloud.ru provider (IAM and API keys, Secret Management, Object Storage, billing, environments) and the self-hosted Alice Cloud control plane.

## Public API

`from cloud import …`: `ensure_default_providers`, `get_registry`.

| Module | Purpose |
| --- | --- |
| [`cloud.base`](base.py) | Provider-neutral cloud abstractions for Logos tooling. |
| [`cloud.cloudru`](cloudru/__init__.py) | Cloud.ru provider: IAM and API keys, Secret Management, Object Storage, billing and environments. |
| [`cloud.tools`](tools.py) | Provider-neutral cloud tools exposed through the universal tool registry. |

Other units reach it only through their own ports:

| Provides | To |
| --- | --- |
| `cloud.base.CloudProviderError` | `economy`, `secret_store` |
| `cloud.cloudru.api_key_provider.CloudRuApiKeyProvider` | `provider_accounts` |
| `cloud.cloudru.iam.CloudRuIamClient` | `provider_accounts` |
| `cloud.cloudru.object_storage.CloudRuObjectStorage` | `storage` |
| `cloud.cloudru.secret_management.CloudRuSecretManagementClient` | `provider_accounts`, `secret_store` |

## Ports

Everything `cloud` uses from other units is a field of `Host` in [`cloud/host.py`](host.py), read only when a function runs. Functions keep their names as call shims; other ports are `bound.<name>`.

| Port | Provided by |
| --- | --- |
| `BudgetLimitExceeded` | `economy.budget_controller.BudgetLimitExceeded` |
| `CloudBudgetGuard` | `economy.budget_controller.CloudBudgetGuard` |
| `StorageCredentialsMissing` | `storage.contracts.StorageCredentialsMissing` |
| `StorageError` | `storage.contracts.StorageError` |
| `StorageObject` | `storage.contracts.StorageObject` |
| `StorageObjectNotFound` | `storage.contracts.StorageObjectNotFound` |
| `StorageProviderUnavailable` | `storage.contracts.StorageProviderUnavailable` |
| `_safe_relative_path` | `storage.contracts.safe_relative_path` |
| `get_current_trace` | `observability.trace_manager.get_current_trace` |
| `secret_storage_ready` | `secret_store.key_manager.secret_storage_ready` |
| `ssh_runtime_exec` | `tool_providers.runtime_tools.ssh_runtime_exec` |
| `store_secret` | `secret_store.key_manager.store_secret` |

## Install

- `composition/wiring.py` registers the `cloud` host factory (`register_all()`, or `register_units("cloud")` for this unit alone); the host is built on first use.
- `composition/web.py` registers the blueprint `cloudru_iam_bp` (`cloud.cloudru.iam_routes`).
- `composition/tool_sources.py` adds the `cloud` tool category from `cloud.tools`.

# `secret_store`

Provider-neutral secret resolution: typed references with redacted values, resolver and backend contracts, the Cloud.ru Secret Management backend, aliases, policy and audit, capability-only delivery to agents, encrypted storage of provider credentials, and deterministic fakes.

## Public API

`from secret_store import …`: `FakeSecretResolver`, `SecretErrorCode`, `SecretRef`, `SecretResolutionError`, `SecretResolver`, `SecretValue`.

| Module | Purpose |
| --- | --- |
| [`secret_store.credential_crypto`](credential_crypto.py) | Encrypted storage boundary for deployment-wide provider credentials. |
| [`secret_store.key_manager`](key_manager.py) | Generic cryptography and secret-key lifecycle manager for Logos. |

Other units reach it only through their own ports:

| Provides | To |
| --- | --- |
| `secret_store.credential_crypto.CredentialCryptoError` | `mcp_server`, `provider_accounts`, `yandex_client_modules` |
| `secret_store.credential_crypto.decrypt_secret` | `agent_runtime`, `mcp_server`, `provider_accounts`, `yandex_client_modules` |
| `secret_store.credential_crypto.encrypt_secret` | `agent_runtime`, `mcp_server`, `provider_accounts`, `yandex_client_modules` |
| `secret_store.credential_crypto.encryption_configured` | `provider_accounts` |
| `secret_store.credential_crypto.fingerprint_key` | `provider_accounts` |
| `secret_store.key_manager.secret_storage_ready` | `cloud` |
| `secret_store.key_manager.store_secret` | `cloud` |

## Ports

Everything `secret_store` uses from other units is a field of `Host` in [`secret_store/host.py`](host.py), read only when a function runs. Functions keep their names as call shims; other ports are `bound.<name>`.

| Port | Provided by |
| --- | --- |
| `CloudProviderError` | `cloud.base.CloudProviderError` |
| `get_current_trace` | `observability.trace_manager.get_current_trace` |
| `get_store` | `storage.db.get_store` |

## Install

- `composition/wiring.py` registers the `secret_store` host factory (`register_all()`, or `register_units("secret_store")` for this unit alone); the host is built on first use.

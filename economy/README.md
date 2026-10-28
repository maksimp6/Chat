# `economy`

The economy of Logos: a paper treasury ledger with a demo top-up and its HTTP API, the shared local identity used for accounting, billing and energy-based compute pricing of execution traces, department budgets, provider pricing snapshots and knowledge economics.

## Public API

`from economy import …`: `billing`, `budget_controller`, `compute_resources`, `knowledge_economics`, `treasury`, `treasury_identity`, `treasury_routes`.

| Module | Purpose |
| --- | --- |
| [`economy.billing`](billing.py) | Billing helpers for ExecutionTrace. |
| [`economy.budget_controller`](budget_controller.py) | Deterministic REAL/DEMO budget controller for department-level spending. |
| [`economy.compute_resources`](compute_resources.py) | Energy-based compute billing for ExecutionTrace. |
| [`economy.knowledge_economics`](knowledge_economics.py) | Semantic economics for knowledge items and agent operations. |
| [`economy.treasury`](treasury.py) | Durable paper balance ledger; no real payment processing. |
| [`economy.treasury_identity`](treasury_identity.py) | Shared local identity for Logos data and accounting; no visitor authentication. |
| [`economy.treasury_routes`](treasury_routes.py) | HTTP API of the treasury: accounts and the demo top-up. |

Other units reach it only through their own ports:

| Provides | To |
| --- | --- |
| `economy.billing.aggregate_billing` | `chat`, `observability` |
| `economy.billing.build_ai_billing_item` | `chat`, `observability` |
| `economy.billing.settle_billing_to_treasury` | `chat` |
| `economy.budget_controller.BudgetLimitExceeded` | `cloud` |
| `economy.budget_controller.CloudBudgetGuard` | `cloud` |
| `economy.compute_resources.build_compute_billing_item` | `observability` |
| `economy.knowledge_economics.KnowledgeCostConfig` | `chat` |
| `economy.knowledge_economics.calculate_knowledge_cost` | `chat` |
| `economy.treasury_identity.TreasuryIdentityError` | `organization`, `remote_runtime` |
| `economy.treasury_identity.get_current_owner_id` | `chat`, `conversations`, `forge`, `mcp_server`, `model_editor`, `organization`, `provider_accounts`, `remote_runtime`, `runtime`, `web_shell`, `yandex_client_modules` |

## Ports

Everything `economy` uses from other units is a field of `Host` in [`economy/host.py`](host.py), read only when a function runs. Functions keep their names as call shims; other ports are `bound.<name>`.

| Port | Provided by |
| --- | --- |
| `ALL_MODELS` | `core.config.ALL_MODELS` |
| `AUDIO_STT_PRICE_PER_SEC` | `core.config.AUDIO_STT_PRICE_PER_SEC` |
| `AUDIO_TTS_PRICE_PER_SEC` | `core.config.AUDIO_TTS_PRICE_PER_SEC` |
| `CloudProviderError` | `cloud.base.CloudProviderError` |
| `KristallError` | `kristall.KristallError` |
| `canonical_model_key` | `core.config.canonical_model_key` |
| `get_current_trace` | `observability.trace_manager.get_current_trace` |
| `get_store` | `storage.records.get_store` |

## Install

- `composition/wiring.py` registers the `economy` host factory (`register_all()`, or `register_units("economy")` for this unit alone); the host is built on first use.
- `composition/web.py` calls `economy.treasury_routes.install(app)`.
- Storage start-up (`web_shell`) runs `economy.treasury.prepare_treasury_records`.

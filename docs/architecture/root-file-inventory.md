# Инвентаризация файлов корня

Задача: [#430](https://github.com/maksimp6/Chat/issues/430).
База инвентаризации: `1575bc3365187592dd6b3034b89051563fc6fa0b` (master).

После переноса служебных утилит остаются **82 Python-файла** в корне.
Это инвентаризация текущих файлов и очередь связных переносов, а не утверждение,
что пакетная миграция завершена. Группы обозначают ответственность; конкретные
пакеты и публичные импорты определяются контрактом каждого следующего PR.

`app.py` и `config.py` остаются в корне по действующему контракту.
`alice_agent_runner.py` и `cli_agent.py` имеют CLI entrypoints, но содержат
реализацию: сначала выделить её в пакет, затем решить судьбу entrypoints.
Остальные файлы — существующий пакетный долг; ни один не объявляется мёртвым
по одному имени. `db_backend.py` и migration-модули не удаляются в этой
структурной работе: их судьба зависит от отдельной принятой миграции хранения.

| Файл сейчас | Ответственность |
| --- | --- |
| [agent_gateway.py](../../agent_gateway.py) | Агенты и выполнение инструментов |
| [alice_agent_runner.py](../../alice_agent_runner.py) | Корневые точки входа и конфигурация |
| [api_contracts.py](../../api_contracts.py) | Агенты и выполнение инструментов |
| [app.py](../../app.py) | Корневые точки входа и конфигурация |
| [archiver.py](../../archiver.py) | Память, данные и диалоги |
| [billing.py](../../billing.py) | Учёт, бюджет и цены |
| [budget_controller.py](../../budget_controller.py) | Учёт, бюджет и цены |
| [budget_repository.py](../../budget_repository.py) | Учёт, бюджет и цены |
| [chatgpt_mcp.py](../../chatgpt_mcp.py) | MCP |
| [cli_agent.py](../../cli_agent.py) | Корневые точки входа и конфигурация |
| [cloudru_api_key_provider.py](../../cloudru_api_key_provider.py) | Провайдеры, credentials и квоты |
| [cloudru_iam.py](../../cloudru_iam.py) | Провайдеры, credentials и квоты |
| [cloudru_iam_routes.py](../../cloudru_iam_routes.py) | Провайдеры, credentials и квоты |
| [compute_resources.py](../../compute_resources.py) | Учёт, бюджет и цены |
| [config.py](../../config.py) | Корневые точки входа и конфигурация |
| [conversation_metadata.py](../../conversation_metadata.py) | Память, данные и диалоги |
| [conversation_ownership.py](../../conversation_ownership.py) | Память, данные и диалоги |
| [credential_crypto.py](../../credential_crypto.py) | Провайдеры, credentials и квоты |
| [db.py](../../db.py) | Память, данные и диалоги |
| [db_backend.py](../../db_backend.py) | Память, данные и диалоги |
| [departments.py](../../departments.py) | Web, идентификация и бизнес |
| [environment_manager.py](../../environment_manager.py) | Сессии, окружения и runtime |
| [environment_routes.py](../../environment_routes.py) | Сессии, окружения и runtime |
| [file_manager.py](../../file_manager.py) | Yandex client |
| [file_routes.py](../../file_routes.py) | Web, идентификация и бизнес |
| [government.py](../../government.py) | Web, идентификация и бизнес |
| [key_manager.py](../../key_manager.py) | Провайдеры, credentials и квоты |
| [knowledge_economics.py](../../knowledge_economics.py) | Учёт, бюджет и цены |
| [local_agent_gateway.py](../../local_agent_gateway.py) | Агенты и выполнение инструментов |
| [local_tool_agent.py](../../local_tool_agent.py) | Агенты и выполнение инструментов |
| [logger.py](../../logger.py) | Трассировка и журналирование |
| [mcp_routes.py](../../mcp_routes.py) | MCP |
| [mcp_storage.py](../../mcp_storage.py) | MCP |
| [mcp_trace.py](../../mcp_trace.py) | MCP |
| [memory_db.py](../../memory_db.py) | Память, данные и диалоги |
| [memory_extractor.py](../../memory_extractor.py) | Память, данные и диалоги |
| [memory_manager.py](../../memory_manager.py) | Память, данные и диалоги |
| [model_discovery.py](../../model_discovery.py) | Провайдеры, credentials и квоты |
| [observability_migrations.py](../../observability_migrations.py) | Память, данные и диалоги |
| [partial_output.py](../../partial_output.py) | Агенты и выполнение инструментов |
| [partner_relations.py](../../partner_relations.py) | Web, идентификация и бизнес |
| [plugin_execution.py](../../plugin_execution.py) | Агенты и выполнение инструментов |
| [plugin_manager.py](../../plugin_manager.py) | Web, идентификация и бизнес |
| [plugin_routes.py](../../plugin_routes.py) | Web, идентификация и бизнес |
| [pricing_registry.py](../../pricing_registry.py) | Учёт, бюджет и цены |
| [project_tree.py](../../project_tree.py) | Web, идентификация и бизнес |
| [provider_credentials.py](../../provider_credentials.py) | Провайдеры, credentials и квоты |
| [provider_credentials_routes.py](../../provider_credentials_routes.py) | Провайдеры, credentials и квоты |
| [provider_key_rotation.py](../../provider_key_rotation.py) | Провайдеры, credentials и квоты |
| [provider_quota_routes.py](../../provider_quota_routes.py) | Провайдеры, credentials и квоты |
| [provider_quotas.py](../../provider_quotas.py) | Провайдеры, credentials и квоты |
| [reasoning_plan.py](../../reasoning_plan.py) | Агенты и выполнение инструментов |
| [responses_tool_loop.py](../../responses_tool_loop.py) | Агенты и выполнение инструментов |
| [runtime_api.py](../../runtime_api.py) | Сессии, окружения и runtime |
| [runtime_migrations.py](../../runtime_migrations.py) | Память, данные и диалоги |
| [runtime_tools.py](../../runtime_tools.py) | Сессии, окружения и runtime |
| [sdk.py](../../sdk.py) | Агенты и выполнение инструментов |
| [session_manager.py](../../session_manager.py) | Сессии, окружения и runtime |
| [session_profiles.py](../../session_profiles.py) | Сессии, окружения и runtime |
| [session_runtime.py](../../session_runtime.py) | Сессии, окружения и runtime |
| [short_token_auth.py](../../short_token_auth.py) | Web, идентификация и бизнес |
| [ssh_runtime.py](../../ssh_runtime.py) | Сессии, окружения и runtime |
| [ssh_runtime_settings.py](../../ssh_runtime_settings.py) | Сессии, окружения и runtime |
| [storage.py](../../storage.py) | Память, данные и диалоги |
| [tool_registry.py](../../tool_registry.py) | Агенты и выполнение инструментов |
| [trace_manager.py](../../trace_manager.py) | Трассировка и журналирование |
| [trace_security.py](../../trace_security.py) | Трассировка и журналирование |
| [trace_timing.py](../../trace_timing.py) | Трассировка и журналирование |
| [treasury.py](../../treasury.py) | Учёт, бюджет и цены |
| [treasury_identity.py](../../treasury_identity.py) | Учёт, бюджет и цены |
| [universal_tool_platform.py](../../universal_tool_platform.py) | Агенты и выполнение инструментов |
| [user_identity.py](../../user_identity.py) | Web, идентификация и бизнес |
| [voice_routes.py](../../voice_routes.py) | Web, идентификация и бизнес |
| [yandex_api_key_provider.py](../../yandex_api_key_provider.py) | Провайдеры, credentials и квоты |
| [yandex_api_logger.py](../../yandex_api_logger.py) | Трассировка и журналирование |
| [yandex_client.py](../../yandex_client.py) | Yandex client |
| [yandex_metadata_validator.py](../../yandex_metadata_validator.py) | Yandex client |
| [yandex_request_builder.py](../../yandex_request_builder.py) | Yandex client |
| [yandex_request_utils.py](../../yandex_request_utils.py) | Yandex client |
| [yandex_response_parser.py](../../yandex_response_parser.py) | Yandex client |
| [yandex_response_poller.py](../../yandex_response_poller.py) | Yandex client |
| [yc_logging.py](../../yc_logging.py) | Трассировка и журналирование |

## Где искать остальные файлы

| Каталог | Содержимое |
| --- | --- |
| `agent_*`, `agent_office/` | Уже выделенные подсистемы агентов |
| `invocation/`, `runtime/` | Контекст вызова и границы runtime |
| `agent_memory/`, `migrations/` | Память агента и существующие migration-файлы |
| `identity/`, `secret_store/` | Идентификация и защищённое хранение |
| `browser/`, `mcp_server/`, `tool_providers/` | Браузер, MCP и локальные tools |
| `printing3d/` | Бизнес печати |
| `static/`, `templates/` | Web assets и шаблоны |
| `android/` | Android client и сборка |
| `scripts/` | Разработка, диагностика и ручные operational helpers |
| `deploy/`, `cloud/`, `config/`, `alice_platform/` | Развёртывание и конфигурация платформы |
| `tests/` | Проверки и fixtures |
| `docs/` | Контракты, инструкции, архитектура |
| `repository_index/` | Индекс репозитория |
| `r/` | Существующие release resources; не перемещены |

Группировка справочная: существующие каталоги в этом PR не переименованы.
Файлы вне Git, пользовательские логи, credentials и резервные копии не затронуты.

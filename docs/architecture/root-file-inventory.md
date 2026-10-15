# Инвентаризация файлов корня

Задача: [#430](https://github.com/maksimp6/Chat/issues/430).
База инвентаризации: ветка `develop`, уборка [#1245](https://github.com/maksimp6/Chat/pull/1245).

После переноса служебных утилит остаются **63 Python-файла** в корне.
Это инвентаризация текущих файлов и очередь связных переносов, а не утверждение,
что пакетная миграция завершена. Группы обозначают ответственность; конкретные
пакеты и публичные импорты определяются контрактом каждого следующего PR.

`app.py` и `config.py` остаются в корне по действующему контракту.
`alice_agent_runner.py` и `cli_agent.py` имеют CLI entrypoints, но содержат
реализацию: сначала выделить её в пакет, затем решить судьбу entrypoints.
Остальные файлы — существующий пакетный долг; ни один не объявляется мёртвым
по одному имени.

| Файл сейчас | Ответственность |
| --- | --- |
| [agent_gateway.py](../../agent_gateway.py) | Агенты и выполнение инструментов |
| [alice_agent_runner.py](../../alice_agent_runner.py) | Корневые точки входа и конфигурация |
| [api_contracts.py](../../api_contracts.py) | Агенты и выполнение инструментов |
| [app.py](../../app.py) | Корневые точки входа и конфигурация |
| [archiver.py](../../archiver.py) | Память, данные и диалоги |
| [cli_agent.py](../../cli_agent.py) | Корневые точки входа и конфигурация |
| [config.py](../../config.py) | Корневые точки входа и конфигурация |
| [conversation_metadata.py](../../conversation_metadata.py) | Память, данные и диалоги |
| [conversation_ownership.py](../../conversation_ownership.py) | Память, данные и диалоги |
| [credential_crypto.py](../../credential_crypto.py) | Провайдеры, credentials и квоты |
| [db.py](../../db.py) | Память, данные и диалоги |
| [departments.py](../../departments.py) | Web, идентификация и бизнес |
| [environment_manager.py](../../environment_manager.py) | Сессии, окружения и runtime |
| [environment_routes.py](../../environment_routes.py) | Сессии, окружения и runtime |
| [file_manager.py](../../file_manager.py) | Yandex client |
| [file_routes.py](../../file_routes.py) | Web, идентификация и бизнес |
| [government.py](../../government.py) | Web, идентификация и бизнес |
| [key_manager.py](../../key_manager.py) | Провайдеры, credentials и квоты |
| [local_agent_gateway.py](../../local_agent_gateway.py) | Агенты и выполнение инструментов |
| [local_tool_agent.py](../../local_tool_agent.py) | Агенты и выполнение инструментов |
| [mcp_routes.py](../../mcp_routes.py) | MCP |
| [memory_db.py](../../memory_db.py) | Память, данные и диалоги |
| [memory_extractor.py](../../memory_extractor.py) | Память, данные и диалоги |
| [memory_manager.py](../../memory_manager.py) | Память, данные и диалоги |
| [model_discovery.py](../../model_discovery.py) | Провайдеры, credentials и квоты |
| [partial_output.py](../../partial_output.py) | Агенты и выполнение инструментов |
| [partner_relations.py](../../partner_relations.py) | Web, идентификация и бизнес |
| [plugin_execution.py](../../plugin_execution.py) | Агенты и выполнение инструментов |
| [plugin_manager.py](../../plugin_manager.py) | Web, идентификация и бизнес |
| [plugin_routes.py](../../plugin_routes.py) | Web, идентификация и бизнес |
| [project_tree.py](../../project_tree.py) | Web, идентификация и бизнес |
| [reasoning_plan.py](../../reasoning_plan.py) | Агенты и выполнение инструментов |
| [responses_tool_loop.py](../../responses_tool_loop.py) | Агенты и выполнение инструментов |
| [runtime_api.py](../../runtime_api.py) | Сессии, окружения и runtime |
| [runtime_tools.py](../../runtime_tools.py) | Сессии, окружения и runtime |
| [sdk.py](../../sdk.py) | Агенты и выполнение инструментов |
| [session_manager.py](../../session_manager.py) | Сессии, окружения и runtime |
| [session_profiles.py](../../session_profiles.py) | Сессии, окружения и runtime |
| [session_runtime.py](../../session_runtime.py) | Сессии, окружения и runtime |
| [ssh_runtime.py](../../ssh_runtime.py) | Сессии, окружения и runtime |
| [ssh_runtime_settings.py](../../ssh_runtime_settings.py) | Сессии, окружения и runtime |
| [storage.py](../../storage.py) | Память, данные и диалоги |
| [tool_registry.py](../../tool_registry.py) | Агенты и выполнение инструментов |
| [universal_tool_platform.py](../../universal_tool_platform.py) | Агенты и выполнение инструментов |
| [voice_routes.py](../../voice_routes.py) | Web, идентификация и бизнес |

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

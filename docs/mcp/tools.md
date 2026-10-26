# Доступные MCP-инструменты

Справочник составлен по реестру `tool_providers/registry.py` (develop, HEAD 869e0ed58).
Все инструменты автоматически экспортируются в MCP (`mcp_server/tools.py::_tools_list`)
с метаданными: risk_level, read_only, requires_approval, capabilities.

Числа в таблице относятся к определениям в словарях источников, а не к сумме
уникальных инструментов работающего приложения. При совпадении имён реестр
оставляет последнее определение: например, `termux_battery_status` переходит из
`termux` в `system`. Строка `system` актуализирована при интеграции PR #1621;
доступность на физическом Android этим справочником не подтверждается.

## Категории реестра

| Категория | Источник | Инструментов | Примеры |
| --- | --- | --- | --- |
| git | tool_providers/git.py | 10 | git_status, git_log, git_diff, git_branches (read, low); git_add, git_commit, git_remote, git_push, git_pull, git_fetch (write, high, requires_approval) |
| termux | tool_providers/termux.py | 11 | termux_battery_status, termux_camera_photo, termux_clipboard_get/set, termux_notification, termux_open_app_settings, termux_toast, termux_torch, termux_tts_speak, termux_vibrate, remove_file |
| system | tool_providers/termux_system.py | 19 (17 + 2 aliases) | get/set_clipboard, get_device_location, get/set_system_volume, get_wifi_status, list_contacts, list_sms, send_sms, send_notification, show_toast, set_screen_brightness, set_torch, take_camera_photo, trigger_vibration, tts_speak, get_system_battery; aliases: termux_battery_status, notification. Private operations retain requires_approval |
| filesystem | tool_providers/filesystem.py | 17 | read_file, write_file, apply_patch, list_directory, glob_search, grep_search, file_stat, calculate_hash, copy_or_move_file, delete_path, make_directory, run_command, http_fetch, get_system_info, python_ast_outline, zip_compress, zip_extract |
| wikipedia | tool_providers/wikipedia.py | 2 | wikipedia_search, wikipedia_summary |
| profiler | tool_providers/profiler.py | 2 | inspect_app_internals, profile_application_performance |
| theme | tool_providers/theme.py | 1 | set_ui_theme (dark/dim/light) |
| runtime | tool_providers/runtime_tools.py | 2 | local_runtime_exec, ssh_runtime_exec (+ ssh_runtime_read_file, ssh_runtime_write_file) |
| government | organization/government.py | 12 | gov.case.create, gov.requirements, gov.collect_data, gov.validate_data, gov.tax_options, gov.prepare_application, gov.prepare_documents, gov.submit, gov.process_response, gov.request_approval, gov.status, gov.archive_case |
| partner | organization/partner_relations.py | 15 | partner.create/list/get/update/delete, partner.contact.create/list/update/delete, partner.message.prepare/send, partner.thread.get, partner.followup.create/complete, partner.status.update |
| cloud | cloud/tools.py | 14 | cloud.resources.list/get, cloud.compute.list/status/start/stop/reboot, cloud.costs.summary, cloud.budget.status, cloud.metrics.query, cloud.logs.query, cloud.backup.create, cloud.ssh.exec, cloud.capabilities |
| forge | forge/tools.py | 6 | printing3d.quote, printing3d.finance.assess/status/plan.set, printing3d.order.create, printing3d.orders.list, printing3d.treasury.summary |

## Bridge-инструменты MCP-сервера (logos_*)

Определены в `mcp_server/tools.py`, доступны через MCP-эндпоинт Logos (streamable-http):

- logos_get_system_status — статус рантайма, MCP, моделей и локальных тула;
- logos_list_agents — агенты Agent Gateway (A2A);
- logos_get_session — жизненный цикл сессии рантайма;
- logos_get_invocation / logos_get_invocation_trace — статус и трассировка вызова;
- logos_get_execution / logos_get_execution_trace — выполнение и трассировка;
- logos_list_conversations / logos_get_conversation / logos_get_conversation_messages — переписки;
- logos_list_project_files / logos_read_project_file / logos_search_project — файлы проекта (read-only).

## Внешние MCP-серверы (deploy/)

- **Remote Desktop Commander (RDC)** — `deploy/remote-desktop-commander/mcp-gateway.mjs`:
  шлюз MCP поверх Desktop Commander (stdio), streamable-http, OAuth + short-token;
  все инструменты апстрима, кроме заблокированного `write_pdf` (BLOCKED_TOOLS);
  дымовые тесты: mcp-gateway-live-smoke.mjs.
- **Chrome Worker (Playwright MCP)** — `deploy/chrome-worker/mcp.mjs`:
  браузерные инструменты через `@playwright/mcp`, сессии в памяти, аутентификация на worker'е.

## Выполнение вызовов

- `tool_providers/universal_tool_platform.py` — единый контракт UniversalToolCall/UniversalToolDefinition;
  transports: responses_api, local_agent, mcp.
- `yandex_client_modules/mcp_mixin.py` — модель (YandexGPT) вызывает инструменты через реестр; трассировка в observability.
- `agent_runtime/gateway.py` — Agent Gateway (A2A-протокол, circuit breaker, permissions, rate limits).

## Что доступно Logos в раннере (issue_runner)

`TOOL_CATEGORIES = ["filesystem"]` — только файловая категория. Полный реестр доступен
в обычном диалоге (app.py runtime). Расширение категорий для раннера — решение владельца.

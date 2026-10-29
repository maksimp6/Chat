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
| browser | browser/tools.py (composition/tool_sources.py) | 17 | browser_open, browser_read, browser_find, browser_extract, browser_click, browser_fill_form, browser_act, browser_wait, browser_evaluate, browser_screenshot, browser_history, browser_tabs, browser_session, browser_cookies, browser_search, browser_request, browser_download |
| rootagent | tool_providers/rootagent.py (composition/tool_sources.py) | 2 | rootagent_run (управление телефоном как юзер: status, probe, capture, tap, swipe, text, key, move, click как задания GitHub Actions на develop), rootagent_result (статус запуска и JSON-результат по run_id). Настройка и границы — [docs/redmi-root-agent.md](../redmi-root-agent.md#logos-tool) |
| tty | tool_providers/tty.py (composition/tool_sources.py) | 6 | tty_open, tty_write, tty_read, tty_resize, tty_close, tty_list |
| cache | tool_providers/cache.py | 3 | cache_append, cache_read, cache_search |

## Отложенная загрузка (defer_loading)

В окне «Инструменты» диалога переключатель **Defer Loading** сохраняет
`tools_config.defer_loading = {"enabled": true, "eager_categories": [...]}`
(`yandex_client_modules/tool_config.py`). Включённый, он отправляет локальные
function-инструменты провайдеру с `defer_loading: true`, кроме категорий из
`eager_categories`: Yandex AI Studio показывает модели отложенный инструмент только
после своего поиска инструментов (элементы `tool_search_call`/`tool_search_output`).
У MCP-сервера тот же флаг — его собственный переключатель «Defer Loading»
(`mcp_server/storage.py`), он уходит в MCP-инструмент запроса вместе с
`server_description`. Встроенные инструменты провайдера (web search, file search,
code interpreter) не откладываются. Выключено (настройки нет) — все инструменты
идут загруженными, как раньше. Сколько инструментов отложено, видно в «👁 Что
видит модель».

## Браузер (категория browser)

Определены в `browser/tools.py` (`BROWSER_TOOLS`), подключаются через
`composition/tool_sources.py` после категории `cloud`; доступны в диалоге и через MCP.
Подробное описание — [browser/README.md](../../browser/README.md).

| Инструмент | Что делает |
| --- | --- |
| browser_open | Открыть адрес во вкладке и сразу получить текст страницы с метками |
| browser_read | Прочитать текущую страницу в нужном виде (view), раздел, элемент или отбор строк |
| browser_find | Найти текст: раздел, ближайшая метка, смещение для browser_read |
| browser_extract | Извлечь элементы по CSS-селектору: текст, Markdown, HTML или атрибуты |
| browser_click | Нажать ссылку, кнопку, флажок; в chrome — также по координатам |
| browser_fill_form | Заполнить форму и отправить её (GET/POST, urlencoded, multipart с файлами) |
| browser_act | type, press, hover, scroll, select, check, uncheck, focus, clear |
| browser_wait | Ждать адрес, элемент, текст, выражение, затишье сети или просто секунды |
| browser_evaluate | Выполнить JavaScript на странице chrome; результат — JSON |
| browser_screenshot | Снимок PNG/JPEG (окно, вся страница, элемент) или PDF |
| browser_history | back, forward, reload, list |
| browser_tabs | list, new, switch, close |
| browser_session | list, status, configure, clear, reset, close |
| browser_cookies | list (без значений), set, delete, clear |
| browser_search | Поиск DuckDuckGo, DuckDuckGo Lite, Bing, all или свой шаблон |
| browser_request | Произвольный HTTP-запрос: метод, заголовки, JSON, форма, multipart, сырое тело |
| browser_download | Скачать файл потоком на диск (url, метка ссылки/изображения или документ вкладки) |

- **Метки.** У ссылок, кнопок, полей, форм, таблиц и изображений в тексте стоят метки
  `[L5]`, `[B2]`, `[I3]`, `[F1]`, `[T1]`, `[M4]`; их передают в `ref`. В chrome метки
  живут в странице и не переиспользуются; после перехода метки новые. `N3` — запрос
  в виде network (chrome).
- **Виды (view).** content (основной текст, по умолчанию), page, text, html, outline,
  links, interactive, forms, tables, images, meta, structured, feed, headers, json,
  network, console; после перехода также none (только сводка).
- **Части текста.** `max_chars` обязателен в инструментах, возвращающих текст: 0 — всё.
  Продолжение — тем же вызовом с `offset=next_offset`. Ничего не обрезается молча:
  всё пропущенное (реклама в поиске, двоичное тело, `max_bytes`) перечисляется в `notes`.
- **Сессии.** У каждого диалога своя сессия (cookies, вкладки, история, настройки);
  вызовы через MCP используют общую сессию `default`; параметр `session` выбирает
  сессию явно. Сессии живут в памяти процесса до reset/close или перезапуска Logos.
- **Движки.** По умолчанию — `http`: быстрый, без JavaScript. `chrome` — настоящий Chrome
  через DevTools Protocol (если установлен); `auto` — сначала http, а страницы, собираемые
  скриптами, и проверки «я не робот» — в chrome. Переключение на chrome и переоткрытие
  страницы в chrome выполняются только по явному запросу движка `auto` или `chrome`
  (аргумент `engine` или `default_engine` в настройках сессии). Действие, которое умеет
  только chrome, во вкладке auto переоткрывает страницу в chrome, и действие повторяют с
  новой меткой. Неизвестный движок — ошибка, а не тихая замена.
- **Типичные цепочки.** browser_search → browser_open(url) → browser_read(view='outline')
  → browser_read(section='2'); browser_open → browser_fill_form(fields, submit=true);
  browser_open → browser_click(ref='L5') → browser_download(ref='L7', path='downloads/',
  overwrite=false); browser_request(url, json_body, json_path) для API.
- **Сохранённый список инструментов.** Если у диалога сохранён список категорий,
  отметьте «Модуль: browser», иначе инструменты браузера модели не предлагаются.
- **Безопасность.** Каждый запрос, перенаправление и каждый запрос Chrome проверяются
  тем же запретом адресов link-local (метаданные облака), что и `http_fetch`. Браузер
  не использует профиль, cookies и входы владельца: у каждой сессии чистое состояние.
  Пароли, токены и значения cookies не показываются модели и не попадают в журналы и
  трассировку.
- **Без подтверждения.** Все инструменты браузера, включая `browser_request`,
  `browser_cookies`, `browser_evaluate` и `browser_wait` с `expression`, выполняются сразу,
  как и остальная платформа (`restrictions_lifted`).
- **TLS.** Проверка сертификатов включена. Отключить её может только настройка владельца
  `ignore_tls_errors` (`browser/settings.py`, по умолчанию выключена): модель этот
  аргумент не передаёт и не меняет.

## Терминал PTY (категория tty)

Определены в `tool_providers/tty.py` (`TTY_TOOLS`), подключаются через
`composition/tool_sources.py` после категории `browser`; доступны в диалоге и через MCP.
Как и у browser, модели они предлагаются только тогда, когда в списке категорий диалога
отмечен «Модуль: tty».

**Не проверено на Redmi (Termux).** Путь PTY пока проверен только чтением кода; запуска
на устройстве и тестов не было (NOT_RUN).

| Инструмент | Что делает |
| --- | --- |
| tty_open | Открыть shell на новом псевдотерминале от имени пользователя приложения; возвращает session_id. Требует подтверждения владельца: без него фаза `approval_required` |
| tty_write | Отправить текст в сессию (строка с `\n` или управляющий символ, например `\u0003` для Ctrl+C). Второго подтверждения нет |
| tty_read | Вывод с последнего чтения: текст UTF-8 (некорректные байты заменяются), признак завершения shell и код выхода; `max_bytes` ограничивает одно чтение, 0 или не указано — без ограничения |
| tty_resize | Изменить размер окна сессии (колонки, строки) |
| tty_close | Закрыть сессию: убить группу процессов shell и освободить псевдотерминал |
| tty_list | Сессии владельца: pid, каталог, размер окна, завершён ли shell |

- **Владелец.** Сессию открывает владелец вызова (`user_id`, иначе владелец диалога из
  трассировки). Остальные инструменты работают только с сессиями того же владельца,
  иначе фаза `authorization`. Вызов без владельца сессию не открывает.
- **Окружение.** shell — значение `SHELL`, если оно абсолютное, иначе `/bin/sh`.
  Окружение — только PATH, HOME, LANG, LC_ALL, USER и TERM=xterm-256color; секреты
  backend в него не попадают. Рабочий каталог — HOME или абсолютный существующий
  каталог, переданный в `cwd`.
- **Права.** shell запускается от пользователя приложения; повышения до root, su и sudo нет.
- **Лимиты.** Таймаута простоя, числа сессий и размера буфера вывода нет (0 — без
  лимита приложения). Сессии живут в памяти процесса; при штатном завершении процесса
  открытые сессии закрываются, при аварийном завершении или SIGTERM — нет.
- **Вывод.** Вывод терминала и аргумент `data` не попадают в трассировку открытым текстом.

## Кэш (категория cache)

Определены в `tool_providers/cache.py` (`CACHE_TOOLS`), собственный источник реестра;
доступны в диалоге, локальному агенту и через MCP, без подтверждения. Кэш — один длинный
текст, который только растёт: в него дописывают модель и инструменты всех диалогов,
ничего не меняется и не удаляется. Файлы рядом с файлом памяти
(`storage.records.memory_file_path`): `logos.cache.md` — сам текст,
`logos.cache.vectors.jsonl` — векторы каждого дописанного куска,
`logos.cache.index.json` — какой файл сейчас лежит в индексе.

| Инструмент | Что делает |
| --- | --- |
| cache_append | Дописать текст в конец (`source`: `model` или имя инструмента). Кусок получает строку-заголовок `## <время> · <источник> · <диалог>`. Он встраивается каждой моделью из `EMBEDDING_MODELS` (text-search-doc, text-search-query, text-embeddings-v2-doc, text-embeddings-v2-query; v2 — в наибольшем размере 768); у каждого вектора хранится URI модели с версией; модель, которая не ответила, — в `embedding_errors`, текст дописывается всё равно. Затем весь текст загружается в индекс одним файлом `logos-cache.md` во все хранилища знаний (`knowledge_vector_store_ids`: `LOGOS_VECTOR_STORE_IDS`, иначе хранилище владельца), которые ищет File Search диалога; прежняя загрузка убирается из хранилищ и удаляется, так что в индексе текст лежит один раз. Состояние — в `index`, сбои — в `index_errors` |
| cache_read | Только весь текст целиком, без параметров: частичного чтения нет |
| cache_search | Поиск через модель-помощника: `query` — что найти, `model` — ключ из `TEXT_MODELS` (выбирает вызывающая модель), `reasoning_effort` — `high` по умолчанию. Помощник получает весь текст кэша и запрос одним запросом провайдера без инструментов и возвращает найденные места дословно с заголовками кусков и краткий вывод; в результате — `answer`, рассуждение, статус, `error`/`incomplete_details`, `usage`. Запрос пишется в трассировку вызывающего хода |

- **Инструкции.** Когда `cache_append` есть в запросе, к инструкциям модели добавляется
  `CACHE_NOTE` (`yandex_client_modules/mcp_mixin.py`): в каждом ходе перед финальным
  ответом дописать в кэш свой ответ целиком и то, как модель видит ход (вход, инструкции,
  инструменты, основания, неуверенность), а ошибки кэша называть в ответе. Это тратит
  токены (четыре эмбеддинга и перезагрузка всего текста в индекс на каждую запись, весь
  текст кэша на входе помощника при каждом поиске) ради диагностики самовосприятия модели.
- **Иконки.** `static/icons/tools/cache_*.svg`, записи в `static/icons/tools/index.json`.
- **Настройки.** Ревизия настроек диалога 4 добавляет категорию `cache` в сохранённый
  выбор категорий; ревизия 6 убирает из него категорию `helper` (ревизии 5), которой
  больше нет.
- **Лимиты.** Размер текста не ограничен (0 — без лимита приложения). Провайдер
  эмбеддингов ограничивает длину куска сам: слишком длинный кусок остаётся без векторов
  с ошибкой в `embedding_errors`; Files API принимает файл до 128 МБ.

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

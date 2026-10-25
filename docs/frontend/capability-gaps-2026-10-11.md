# Logos Pro: аудит пользовательских сценариев

Дата: **2026-10-11**. У Logos Pro есть web-оболочка с чатом, Trace и настройками.
Основные пробелы — честное отображение ошибок, применение выбранных возможностей
и переход от запуска работы к проверенному результату. Часть экранов требует
backend-контракта; это не только frontend-задачи.

## База и метод

| Срез | Revision | Проверка |
| --- | --- | --- |
| master | `51aaf8273f4f465fe78aa0a79e76a22d8f141e35` | Checkout, routes, templates, JS, tests, docs, локальный UI |
| develop | `9d658587871007a041f1981c084a80d2de6d78bd` | Прямой diff деревьев: только Dependency Review workflow и два теста; приложение идентично master |
| Model Editor #1098 | `5426227c8d621cad771a74014de5cc4804c7951f` | Open Draft, фактическая base master; code/docs прочитаны |
| Slicer #1101 | `c597beea512f18b518512947c70422530030995d` | Open Draft, base develop; script/workflow прочитаны |
| Preflight #1109 | `d535922a5dfd16aa59efb080b5252df5eab8cebf` | Open Draft, base develop; code/skills прочитаны |

Сравнение — `git diff origin/master origin/develop`, не только merge-base diff.
PR metadata важнее старого текста: #1102 уже merged в develop, хотя его body
сохраняет «Draft/no merge». Код в draft не считается доступным продуктом.

**Evidence:** source audit + offline HTTP/restart smoke + ограниченный локальный
Chromium smoke с синтетическими данными. Это не source-only аудит и не production
acceptance. Внешние browser requests заблокированы; реальные providers, OAuth
consent, устройства, принтеры и deployment не вызывались.

Фактический entrypoint — [app.py](../../app.py), `/`,
[index.html](../../templates/index.html) и [static modules](../../static/core.js).
Один shell содержит sidebar/modals; deep link — `/?conversation_id=…`, backend
умеет передать выбранный разговор в template.
`../../r/frontend/src/App.tsx` — Vite/React starter с counter,
не второй готовый Logos frontend. Service-local страницы и native Android UI
учтены отдельно.

## Существующие возможности

| Сценарий | UI / component | Backend / CLI / tool и граница |
| --- | --- | --- |
| Чат, история, approval | [chat.js](../../static/chat.js), [sidebar.js](../../static/sidebar.js), [models.js](../../static/models.js) | [mcp_routes.py](../../chat/mcp_routes.py): chat, conversations/messages, execute-approved. Ответы, ошибки и approval cards есть |
| Execution Trace | [execution_surface.js](../../static/execution_surface.js), [trace_viewer.js](../../static/trace_viewer.js), [trace_download.js](../../static/trace_download.js) | `../../runtime_api.py`: invocation status/trace; [invocation/api.py](../../invocation/api.py) safe projection. Compact summary, timeline, viewer/export есть |
| Настройки/tools/skills | [settings_modal.js](../../static/settings/settings_modal.js), [settings_params.js](../../static/settings/settings_params.js), [settings_storage.js](../../static/settings/settings_storage.js) | Settings/tools API, [SkillRegistry](../../agent_skills/registry.py), `/api/skills`. Sampling/tools controls есть; skill selection не подключён к chat request |
| MCP servers | [settings_mcp.js](../../static/settings/settings_mcp.js): add/edit/delete | `/api/mcp-servers` в mcp_routes.py; CRUD не является OAuth/discovery/health workflow |
| Provider connection | [provider_credentials.js](../../static/provider_credentials.js) | `../../provider_credentials_routes.py`: credentials/status; подключение и ошибки уже имеют UI |
| OAuth/browser | GitHub login link; service consent/takeover pages; `../../android/app/src/main/java/com/logospro/mobile/BrowserTakeoverActivity.kt` | `../../identity/github_oauth.py`, `../../deploy/chrome-worker/server.mjs`, `../../deploy/chrome-worker/oauth.mjs`, `../../deploy/remote-desktop-commander/oauth.mjs`. Browser status/control/takeover есть в отдельном сервисе; deployment не проверен |
| Устройства/runtime | SSH settings/test modal; Android local-agent UI | `../../local_agent_gateway.py`: register/heartbeat/poll/result/health; [pairing.py](../../rdc_connection/pairing.py): `/rdc`. Health требует bootstrap authority; redirect может вернуть 401/503 |
| Память | [memory_panel.js](../../static/memory_panel.js), server-rendered modal | [app.py](../../app.py): manage/config/clear. Facts, enable/limit/clear есть |
| Backup/replay | Web controls не найдены | `../../agent_memory/backup.py`, `../../agent_memory/file_memory_db.py`, [backup tests](../../tests/test_memory_backup.py). Library capabilities, не operational HTTP restore API |
| Бюджеты/ошибки | [treasury.js](../../static/treasury.js), execution summary, [system_status.js](../../static/system_status.js) | Treasury API, `../../provider_quota_routes.py`, [budget_controller.py](../../economy/budget_controller.py). Paper balance, provider quota и budget policy — разные сущности |
| 3D business | Chat tools; dashboard отсутствует | `../../printing3d/routes.py`: quote/orders/status/settle/summary/finance; `../../printing3d/tools.py`. Это business slice, не printer controller |
| Model Editor / slicing / transfer | В master/develop нет viewport/scene editor/print-job UI | #1098/#1101/#1109 только drafts; [printing3d.md](../printing3d.md). Production transport/queue не доказаны |
| Задачи/результаты | Chat Execution Surface есть; task workbench нет | [agent_shell CLI](../../agent_shell/__main__.py), [store.py](../../agent_shell/store.py), [task_state.py](../../agent_office/task_state.py). CLI и GitHub evidence lifecycle отдельны; общего web task API в проверенных routes нет |

## Gaps и критерии приёмки

**P1** — мешает понять результат или безопасно продолжить работу; **P2** —
расширяет сценарий после базовых контрактов. Это предлагаемые приоритеты.
Security/ownership нужны до UI writes. Отсутствующая control-кнопка не всегда
дефект: backend сначала должен безопасно поддержать действие.

### G01 / P1 — восстановление после таймаута чата

**Тип:** frontend + неполный backend handoff. [chat.js](../../static/chat.js)
не использует invocation status/trace endpoints из `../../runtime_api.py`.
[dispatcher.js](../../static/dispatcher.js) по умолчанию ограничивает запрос
10 секундами через `AbortSignal.timeout`, если браузер это поддерживает.
Client timeout не доказывает остановку server execution. Синхронный chat path
не гарантирует получения invocation ID до обрыва.

**Не хватает:** «исход неизвестен → проверить уже запущенную работу», correlation
ID и частичного результата без слепой повторной отправки.

**Приёмка:** late response/reload/disconnect не создают дубликат по automatic retry;
UI получает существующие status/trace и различает failed/partial/blocked/done/unknown.
Сначала согласовать early correlation, idempotency и ownership; не добавлять
несуществующий cancel API. Переиспользовать runtime/Trace services.

### G02 / P1 — MCP 503 выглядит как отсутствие серверов

**Тип:** UI-only defect, воспроизведён. `fetchMcpServers()` в
[settings_mcp.js](../../static/settings/settings_mcp.js) превращает non-2xx/network
failure в пустой массив; `renderList()` показывает «Нет MCP серверов».

**Не хватает:** loading/empty/unauthorized/unavailable, read-only retry и stale
состояния последнего достоверного списка.

**Приёмка:** только пустой 200 показывает empty; 401/503/network error дают
объяснение и подходящий next step. Retry возвращает список без создания сервера.
DELETE тоже должен проверять HTTP status, а не только resolved Promise.

### G03 / P1 — ошибки памяти не объясняются внутри панели

**Тип:** UI-only defect, воспроизведён. Catch в load/save/clear у
[memory_panel.js](../../static/memory_panel.js) пишет только console error.
Общий network indicator не объясняет результат операции с facts.

**Не хватает:** inline error/stale state, retry, подтверждения результата save/clear.

**Приёмка:** loading failure не выглядит как отсутствие памяти; failed save
не выглядит как применённая настройка; failed clear сохраняет последнее
достоверное состояние. Проверить 401/503/bad payload/recovery и озвучивание ошибки.

### G04 / P1 — выбор проектных skills не доходит до chat request

**Тип:** UI/settings integration. [mcp_routes.py](../../chat/mcp_routes.py) имеет
`/api/skills`, принимает `skills` из request/params, применяет role policy и пишет
metadata в Trace. `selected_skills` существует в
[settings_storage.js](../../static/settings/settings_storage.js), но application JS
не использует `/api/skills`, а [settings_params.js](../../static/settings/settings_params.js)
не передаёт выбор.

**Не хватает:** каталога доступных role skills, явного выбора и связи с invocation.

**Приёмка:** выбор переживает reload и попадает в поддержанное поле запроса;
deselect удаляет его; Trace содержит name/source/version без тела навыка.
Недоступная role/skill и превышение лимита дают ясную ошибку. Новые пакеты этого
документационного PR не обходят allowlist. Tools controls дополнительно требуют
targeted payload tests; исторический общий дефект tools_config не объявляется
сегодняшним без такой проверки.

### G05 / P1 — provider quota невидима до отказа

**Тип:** преимущественно UI; self-read backend существует.
`../../provider_quota_routes.py` имеет
`GET /api/provider-quota/me`, но UI его не вызывает. Quota blocker в Execution
Surface не заменяет usage/limit view.

**Не хватает:** authenticated self-quota summary, периода/остатка из API,
объяснения отказа и разрешённого next step.

**Приёмка:** UI отражает API values, различает 401/unknown/denied; 429 сохраняет
ввод и объясняет повторную попытку по доступным данным. Admin policy token не
передаётся browser. Treasury balance и calculated cost не выдаются за spend
allowance. Для BudgetController нужен отдельно согласованный user-safe API.

### G06 / P1 — нет общей безопасной панели устройств

**Тип:** UI + owner-facing API/security adapter.
`../../local_agent_gateway.py` хранит heartbeat/capabilities,
но health требует bootstrap token и перечисляет агентов. Web device list нет.
`../../android/app/src/main/java/com/logospro/mobile/MainActivity.kt`
уже умеет подключать local agent; нельзя утверждать, что device UI отсутствует вообще.

**Не хватает:** выбранного устройства, capability, last-seen/stale и понятной
диагностики в рамках owner identity.

**Приёмка:** сначала scoped read-only projection; wrong-owner отказ, отсутствие
bootstrap secrets в UI, явные offline/stale/unknown. Открытие панели не активирует
устройство. Pair/revoke требуют отдельного backend-контракта; `/rdc` redirect
его не заменяет. Owners: [#650](https://github.com/maksimp6/Chat/issues/650),
[#787](https://github.com/maksimp6/Chat/issues/787).

### G07 / P1 — OAuth/MCP/browser onboarding разрознен

**Тип:** cross-service UI + integration backend. GitHub login, MCP CRUD,
service-local consent/takeover уже есть; ссылки на реализацию — в карте выше.
Они не образуют web-путь «подключить → проверить discovery → увидеть grants →
исправить ошибку».

**Не хватает:** safe connection summary, read/control distinction, expired auth,
недоступного adapter и перехода к подходящему существующему flow.

**Приёмка:** disconnected/authorizing/connected/expired/unavailable выведены из
verified state; read-only grant не даёт control; discover failure не называется
OAuth failure. Test lane доказывает login/discovery/read и ожидаемый control denial.
Нужен safe aggregation/session projection API без admin/static credentials в browser.
Service-local pages не переписывать. Production acceptance отдельно:
[#326](https://github.com/maksimp6/Chat/issues/326), [#409](https://github.com/maksimp6/Chat/issues/409).

### G08 / P1 — backup/replay без owner-facing workflow

**Тип:** library есть; operational API и UI нет. `../../agent_memory/backup.py`
реализует create/restore/copy verification, FileMemoryDB — journal recovery.
Memory facts modal решает другую задачу.

**Не хватает:** backup identity, даты проверки, authority и изолированной
restore/replay validation; не кнопки неподтверждённого live restore.

**Приёмка:** сначала scoped read-only manifest/verification API; UI показывает
источник/hash/invariants и unknown. Isolated restore проверяет инварианты;
corrupt/foreign snapshot отвергается. Restore/cutover controls — только после
контракта и approval. Owners [#776](https://github.com/maksimp6/Chat/issues/776),
[#783](https://github.com/maksimp6/Chat/issues/783). Draft #1054/#1083/#1106 —
отдельные engine/replay контракты, не завершённая текущая миграция.

### G09 / P1 — задачи не связаны с проверенным результатом

**Тип:** backend lifecycle/API reconciliation + UI.
[agent_shell CLI](../../agent_shell/__main__.py) имеет add/list/run-next/approve/show/recover;
[task_state.py](../../agent_office/task_state.py) выводит status из execution,
deliverable, validation и review evidence. Это разные модели, не единый web API.

**Не хватает:** owner/stage/blocker/approval/artifact/verification/next step
в одной карточке, sanitized trace без сырых handler results.

**Приёмка:** canonical lifecycle определён до UI; acknowledged/queued не
становятся working без backend evidence, completed не становится done без
deliverable/validation. Missing artifact, needs-approval, failed, stalled и
partial различены. Артефакт доступен пользователю, а не только worker filesystem.
Не создавать третью очередь. Owners [#604](https://github.com/maksimp6/Chat/issues/604),
[#576](https://github.com/maksimp6/Chat/issues/576).

### G10 / P2 — 3D business API без review/dashboard UI

**Тип:** UI gap при существующем business backend.
`../../printing3d/routes.py` и `../../printing3d/tools.py`
дают quote/orders/settlement/P&L/finance, но static/template не содержит `/api/3d` consumer.

**Не хватает:** review quote с inputs/unknowns, order list, понятных status/settlement
действий и per-currency summary.

**Приёмка:** значения совпадают с API, чужие заказы недоступны, writes соблюдают
утверждённые auth/approval boundaries; empty/error различены. Никаких автоматических
покупок, банковских операций или печати. Owner [#552](https://github.com/maksimp6/Chat/issues/552).

### G11 / P2 — Model Editor без viewport и artifact review

**Тип:** draft backend + отсутствующий UI. [#1098](https://github.com/maksimp6/Chat/pull/1098)
содержит [engine.py](https://github.com/maksimp6/Chat/blob/5426227c8d621cad771a74014de5cc4804c7951f/model_editor/engine.py),
store и шесть local tools; frontend diff отсутствует. Cube/cylinder transforms,
immutable scenes и STL export не дают arbitrary import, booleans, STEP/3MF.

**Не хватает:** actual-geometry render, object selection, units/dimensions,
revision/history, scoped download и printability limitations.

**Приёмка:** после backend/tool/storage acceptance viewport показывает exact
revision, move/scale меняет нужный объект, rollback проверяем, скачанный STL
совпадает с hash. Пересечение примитивов не называется Boolean union, bounding
box — физической посадкой. Owners #1097/#1079; live deployed end-to-end не проверен.

### G12 / P2 — model → slice → stage → verify не интегрированы

**Тип:** backend integration/transport gaps + будущий UI.
[#1101](https://github.com/maksimp6/Chat/pull/1101) —
[calibration-cube script](https://github.com/maksimp6/Chat/blob/c597beea512f18b518512947c70422530030995d/scripts/a1mini_pla_cube_slice.py)
и workflow proof, не общий slicer/job API. [#1109](https://github.com/maksimp6/Chat/pull/1109)
даёт [offline plate inspection](https://github.com/maksimp6/Chat/blob/d535922a5dfd16aa59efb080b5252df5eab8cebf/printing3d/plate.py),
не upload/controller. Оба Draft.

**Не хватает:** persistent job, approved machine/material settings, audit/warnings,
single-file delivery, отдельно разрешённого staging и hash readback.

**Приёмка:** exact model revision, slicer version/effective settings, artifact hash,
preflight и независимый readback status видны. Timeout не вызывает blind re-upload.
Unsliced/partial/mismatch/full-storage/offline дают blocker. Upload не нагревает
и не запускает printer; start и наблюдение — отдельные capabilities.
READY_FOR_USER/PRINTING/COMPLETE различены. Owners #1096/#1108; никаких физических
действий этот аудит не выполняет.

Координация существующих потоков — [#1112](https://github.com/maksimp6/Chat/issues/1112).
[Draft #1114](https://github.com/maksimp6/Chat/pull/1114), head
`3e10c880a1b056ce7ee578960307ee0bd8b920e7`, добавляет 17-record asset catalog,
а не UI/job API. Его `test_tube_holder.3mf` и модель из
[#1110](https://github.com/maksimp6/Chat/pull/1110) — unsliced sources; локальная
проверка ZIP CRC и отсутствия G-code это подтверждает. Не импортировать эти
ветки в UI PR и не выдавать asset presence за print readiness.

## Предлагаемый порядок

1. G02/G03: ошибки в существующих панелях.
2. G04/G05: skills-to-request и read-only self-quota.
3. G01: early correlation/ownership, затем recovery UI.
4. G06/G07: safe connection/device API, затем onboarding/status UI.
5. G08/G09: authority/lifecycle API, затем read-only evidence panels; writes позже.
6. G10: business review UI на существующих services.
7. G11/G12: после backend acceptance, geometry → slice/preflight → delivery;
   физическое управление отдельным этапом.

Каждая реализация требует отдельного scope и tests. Аудит не создаёт Issues,
не реализует gaps и не предлагает автоматически сливать Draft PR.

## Локальные проверки

Disposable SQLite/Memory paths, пустое окружение без provider credentials,
локальные assets, новый профиль Chromium; production/device data не использовались.

| Проверка | Результат | Ограничение |
| --- | --- | --- |
| `python scripts/local_launch_smoke.py --offline` | exit 0, **PARTIAL**; health/root/session/restart true | Нет provider configuration, real chat, successful persisted chat trace |
| 70 focused pytest cases | PASS | Не full suite/live interoperability |
| Пять Node test-файлов | PASS | BrowserShim/VM не равен браузерному render |
| Chromium shell, memory empty, provider dialog, MCP empty | PASS | Credentials не сохранялись, подключений не было |
| Chromium MCP 503 | G02 воспроизведён: ложный empty | Контролируемая подмена ответа |
| Chromium memory 503 | G03 воспроизведён: нет inline error | Не реальная авария storage |
| Chromium chat 429 и network abort | Ошибки видимы | Mocked response; G01 подтверждён source, не live provider |
| Chromium 390 × 844 | Input видим | Не полный mobile/a11y аудит |

Focused commands из корня репозитория:

```bash
python -m pytest -q tests/test_frontend_chat_contract.py tests/test_frontend_navigation_contract.py tests/test_memory_panel_contract.py tests/test_runtime_api.py tests/test_provider_quotas.py tests/test_memory_backup.py tests/test_agent_task_state.py
node --test tests/test_execution_surface.js tests/test_frontend_smoke.js tests/test_frontend_boot.js tests/test_frontend_runtime_safety.js tests/test_real_header_actions.js
```

Chromium reproduction: открыть локальный `/`, проверить `#msg-input`; нажать
`#memory-btn` и получить empty state; открыть credentials dialog без save;
открыть MCP manager и сравнить пустой 200 с подменённым 503. Для памяти подменить
`/api/memory/manage` на 503 и проверить `#memoryFactsList`. Для chat использовать
synthetic conversation query и перехват `/api/chat`: JSON error с 429, затем
network abort; проверить видимые ошибки. Внешние requests блокировать. Повторный
запуск оценивает свой фактический head, а не повторяет статусы таблицы.

Не проверены: production deployment/version, live OAuth/session recovery,
model/tool calls, все keyboard/focus states, реальные мобильные устройства,
hardware/printer operations, полная crash/recovery матрица. Старый
[progressive enhancement audit](progressive-enhancement-audit.md) остаётся
историческим срезом; его findings автоматически не повторены.

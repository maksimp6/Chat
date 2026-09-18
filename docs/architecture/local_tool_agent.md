# Cloud Gateway и Local Tool Agent

Alice Pro разделяет центральный Gateway и локальный исполнитель.

## Поток

\`\`\`text
ChatGPT / UI / Agent
        |
        | HTTPS / MCP
        v
Cloud.ru Alice Pro Gateway
        |
        | persistent local-agent job queue
        v
Second Android Phone
        |
        v
Universal Tool Registry / local executor
\`\`\`

Телефон делает исходящие HTTPS-запросы к Cloud.ru. Входящий порт на телефоне и проброс портов домашнего роутера не нужны.

## Регистрация

На Cloud.ru задаётся:

\`\`\`env
ALICE_LOCAL_AGENT_BOOTSTRAP_TOKEN=<long-random-secret>
\`\`\`

Телефон использует bootstrap token для \`POST /api/local-agents/register\`. Сервер выдаёт отдельный runtime token конкретному агенту. Bootstrap token не используется для выполнения заданий.

Runtime token передаётся только в \`Authorization: Bearer ...\` и хранится хэшированным на сервере.

## Протокол

\`POST /api/local-agents/register\`

\`POST /api/local-agents/<agent_id>/heartbeat\`

\`GET /api/local-agents/<agent_id>/poll\`

\`POST /api/local-agents/<agent_id>/jobs/<job_id>/result\`

Задание содержит имя универсального инструмента, аргументы, metadata и идентификаторы trace/invocation.

Очередь сохраняется в SQLite, задания получают короткую lease. Если телефон исчезает, просроченное задание возвращается в \`queued\`.

## Безопасность

Публично не открываются ADB и внутренние Android-порты. Агент сам инициирует соединение наружу.

Инструмент не должен попадать в очередь в обход Universal Tool Executor/policy. Текущий MVP предоставляет очередь как внутренний Python API \`enqueue_local_tool_job()\`; интеграция с единым Executor выполняется в #127.

Секреты не записываются в ExecutionTrace, результаты и обычные логи.

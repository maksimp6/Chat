# Роли и границы агентов

В Alice Pro «агент» — это роль/исполнитель поверх общих runtime boundaries, а не отдельный приватный стек с собственной моделью, очередью, базой, авторизацией и логированием.

## Общий execution contract

Агентные пути должны переиспользовать:

- Agent Gateway / RuntimeDispatcher для runtime-scoped исполнения;
- InvocationContext для owner/runtime correlation;
- Universal Tool Registry / UniversalToolExecutor для инструментов и approval boundary;
- ExecutionTrace для проверяемого lifecycle/evidence;
- canonical provider/model pipeline вместо прямых обходных model calls;
- canonical Secret Store (#755) и durable-state boundary (#776).

Новый агент не должен создавать параллельные execution, authorization, tracing или credential layers.

## Существующие формы

### Web/chat

Пользовательский чат входит через backend HTTP path и использует общий model/tool lifecycle. Это транспорт/UI, а не отдельный «чат-агент» со своей SQLite-очередью.

### CLI/Termux

`cli_agent.py` — транспортный адаптер к backend Alice Pro. Он не должен хранить provider key или создавать отдельный локальный model/tool loop.

### MCP

MCP — внешний transport/tool surface. Он не является главным координатором всех остальных агентов. MCP-вызовы должны сходиться к тем же authorization/tool/trace boundaries.

### Alice GitHub agent

Issue/label-triggered coding-agent workflow имеет отдельный documented lifecycle, но изменения репозитория всё равно проходят branch/PR/CI/review gates. См. [alice-github-agent.md](alice-github-agent.md).

### Agent shell

`agent_shell/` — отдельный starter task-runner, документированный в [agent-shell.md](agent-shell.md). Его SQLite task store не является общей очередью всех Alice agents и не является canonical application storage.

## Storage и secrets

Не существует правила «все агенты общаются через общую очередь SQLite». Storage migration принадлежит #776 и выполняется consumer-by-consumer.

Secret values не задаются агентам через `config.py` и не должны попадать в prompts, task payloads, ordinary tool results, logs или ExecutionTrace. Используется canonical Secret Store boundary #755; legacy consumers мигрируются отдельно.

## Логи и наблюдаемость

Не полагайтесь на исторические имена `chat.txt`, `voice.txt`, `search.txt` или `api_debug.txt` как на универсальный agent contract. Для проверяемого выполнения основным application evidence служат Invocation/ExecutionTrace и специализированные runtime/workflow evidence конкретной поверхности.

## Добавление нового агента

Новый агент должен сначала определить:

1. owner/runtime boundary;
2. входной transport/trigger;
3. разрешённые tools/capabilities;
4. approval policy для consequential actions;
5. canonical storage refs;
6. secret-resolution boundary;
7. ExecutionTrace/evidence;
8. timeout/cancellation/cost limits.

Только после этого добавляется специализированная логика агента.

# Общая архитектура проекта Alice Pro

## Высокоуровневая схема

```text
User / Android / external client
              |
              v
       Flask HTTP / MCP
              |
              v
       InvocationContext
        /      |       \
       v       v        v
 Yandex     Universal   RuntimeDispatcher
 Responses  ToolExecutor      |
              |               v
              v          ExecutionTrace
        MCP / local tools      |
                              v
                        billing/evidence
```

Transport не определяет отдельную архитектуру исполнения: web, CLI и MCP должны сходиться к общим owner/runtime, tool/policy и trace boundaries.

## Основные компоненты

### Web UI

Основной UI — repository-local HTML/JavaScript/CSS из `templates/` и `static/`. Android использует WebView-путь. Frontend не хранит provider secrets и не создаёт отдельный model/tool runtime.

### Backend

`app.py` собирает Flask application. Route modules принимают HTTP-запросы, но provider/tool execution должен сохранять общий Invocation/Trace lifecycle.

### Model/provider pipeline

Yandex AI Studio Responses API — основной model provider path. Interface adapters не должны обходить canonical provider pipeline прямыми model calls.

### Tools и MCP

MCP и local tools сходятся к Universal Tool Registry / UniversalToolExecutor. Approval-required операции не становятся разрешёнными из-за другого транспорта.

### Runtime scope и evidence

RuntimeDispatcher сохраняет owner/runtime isolation. ExecutionTrace фиксирует provider/tool lifecycle, timing, errors и billing correlation. Обычные текстовые логи могут помогать диагностике, но не являются заменой canonical execution evidence.

## Storage

Storage сейчас переходный:

- legacy SQLite/PostgreSQL остаются у ещё не мигрированных consumers;
- FileMemoryDB уже является authoritative для отдельных durable aggregates;
- миграция consumer-by-consumer принадлежит #776.

Поэтому ни «SQLite — единственное основное хранилище», ни «SQL полностью удалён» не описывают текущий master корректно.

Secret values не принадлежат application storage. Их canonical boundary — #755.

## Agents

Агенты являются специализированными ролями/исполнителями поверх общих runtime boundaries. Они не должны создавать параллельные базы, authorization layers, tool executors или tracing systems. Подробности: [agents overview](../agents/overview.md).

## Platform и production

`config/alice/` — desired state, `alice_platform/` — platform contract. Успешная config validation не доказывает Cloud.ru deployment. Provider-backed convergence принадлежит #783.

Аналогично наличие OAuth/MCP/browser кода и локальных тестов не доказывает production interoperability; для внешних поверхностей требуется отдельное live acceptance evidence.

## Технологический стек

- Backend: Python, Flask.
- Frontend: HTML, JavaScript, CSS; Android WebView client.
- AI: Yandex AI Studio Responses API.
- Tools: Universal tool execution поверх MCP/local providers.
- Storage: переходный legacy SQL + file-native Memory DB.
- Observability/evidence: InvocationContext + ExecutionTrace, дополненные специализированными логами/CI evidence.
- Infrastructure: Cloud.ru integrations и config-driven Alice Platform, с fail-closed separation между desired state и live provider evidence.

## Связанные документы

- [Backend](../backend/overview.md)
- [Database/storage](../database/overview.md)
- [Memory](../memory/overview.md)
- [MCP](../mcp/overview.md)
- [Agents](../agents/overview.md)
- [Platform](../platform/architecture.md)
- [Security](../security/overview.md)
- [Execution Trace lifecycle](../execution-trace-lifecycle.md)

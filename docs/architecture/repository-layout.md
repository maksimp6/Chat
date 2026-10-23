# Структура репозитория

Issue: #430

Alice Pro больше не должна расти как набор несвязанных Python-файлов в корне.
Корень предназначен для project-level metadata, конфигурации и минимального числа
реальных entrypoints. Доменные модули живут в пакетах.

## Правила

- Новые предметные Python-модули в корне запрещены.
- Один PR переносит один связный кластер и обновляет все импорты и тесты.
- Постоянные compatibility stubs в корне не оставляем.
- `app.py` остаётся Flask entrypoint.
- `config.py` пока остаётся project-level конфигурацией.
- Остальные существующие root-модули считаются legacy debt и мигрируют волнами.
- Shell/debug helpers должны жить в `scripts/`, кроме реального startup entrypoint.
- Каждый перенос проходит полный CI до merge.

## Целевая раскладка

```text
agents/        agent context, gateways, runners, tools
browser/       browser capability contracts and adapters
invocation/    request-scoped context, lifecycle, API, trace binding
mcp/           MCP transport, storage, routes, trace adapters and MCP tools
providers/     credentials, quota and key rotation
treasury/      billing, budget, pricing and treasury domain
memory/        memory persistence/extraction/management
sessions/      session lifecycle, profiles and runtime state
tracing/       execution trace, security, timing and mirrors
yandex/        Yandex provider/client/request/response implementation
web/routes/    Flask route blueprints
scripts/       operational and developer shell utilities
```

## Первая волна

`invocation_api.py`, `invocation_context.py`, `invocation_manager.py` и
`invocation_trace.py` перенесены в пакет `invocation/`.

Новые импорты:

```python
from invocation.api import get_invocation_status
from invocation.context import InvocationContext
from invocation.manager import create_invocation
from invocation.trace import create_invocation_trace
```

## Guardrail

`tests/test_repository_root_layout.py` фиксирует текущий legacy baseline.
Тест запрещает добавлять новые root Python-файлы. При следующей миграции имя
перенесённого файла удаляется из legacy allowlist. Технический долг может только
уменьшаться, а не расти.

## Следующие волны

1. agents;
2. tracing;
3. sessions + memory;
4. providers;
5. MCP;
6. Yandex provider/client;
7. treasury/billing;
8. Flask routes;
9. shell helpers в `scripts/`.

После каждой волны обновляются этот документ и guardrail allowlist.

## Вторая волна

`browser_adapters.py` и `browser_capabilities.py` перенесены в пакет `browser/`.
Тесты и архитектурная документация используют новые package imports.

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

## Текущая раскладка

```text
agent_runtime/      шлюзы агентов, локальный агент, CLI, цикл Responses, reasoning plan
agent_context/      контекст и кэш агентов
agent_memory/       хранилища памяти агентов
agent_retrieval/    поиск по репозиторию для агентов
agent_skills/       навыки агентов
browser/            контракты и адаптеры браузера
cloud/              интеграция Cloud.ru (IAM, secret management, клиенты)
conversations/      метаданные и владение беседами
economy/            billing, budget, pricing, treasury, compute и knowledge economics
identity/           пользователи, GitHub OAuth, короткие токены
invocation/         контекст запроса, жизненный цикл, API, привязка трассы
mcp_server/         транспорт, хранилище и мост MCP
observability/      Execution Trace (trace_manager, security, timing) и системный лог
organization/       government, departments, partner relations
plugin_system/      менеджер, маршруты и выполнение плагинов
provider_accounts/  credentials, квоты и ротация ключей провайдеров
remote_runtime/     SSH runtime, окружения и их маршруты
sessions/           менеджер сессий, профили и runtime
workspace/          файлы, дерево проекта и их маршруты
yandex_client_modules/  клиент и реализация провайдера Yandex
scripts/            операционные и developer-утилиты
```

Диагностика устроена в два уровня: Execution Trace — основа диагностики
выполнения, а `observability/system_log.py` — небольшой системный лог для запуска
сервера и сбоев до появления Trace. Подробности — в
[логировании](../backend/logging.md).

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

## Волны переноса

| Волна | Пакеты | PR |
| --- | --- | --- |
| Trace и логирование | `observability/` | #1250 |
| Экономика и провайдеры | `economy/`, `provider_accounts/` | #1255 |
| Агенты, сессии, удалённый runtime | `agent_runtime/`, `sessions/`, `remote_runtime/` | #1267 |
| Файлы, плагины, организация, беседы | `workspace/`, `plugin_system/`, `organization/`, `conversations/` | #1268 |

Корневых Python-файлов осталось 21: точки входа (`app.py`, `issue_runner.py`),
конфигурация (`config.py`), базовая инфраструктура (`db.py`, `storage.py`,
`sdk.py`, `memory_*`) и реестр/платформа инструментов (`tool_registry.py`,
`universal_tool_platform.py`, `runtime_api.py`, `runtime_tools.py`,
`mcp_routes.py`, `voice_routes.py`). Их перенос требует отдельных контрактов,
потому что многие из них — точки входа или общий базовый слой.

После каждой волны обновляются этот документ и guardrail allowlist.

## Вторая волна

`browser_adapters.py` и `browser_capabilities.py` перенесены в пакет `browser/`.
Тесты и архитектурная документация используют новые package imports.

## Служебные утилиты (2026-10-14)

Девять shell helpers и `send_logs.py` перенесены из корня в `scripts/`.
В корне остаётся только startup shell entrypoint `start.sh`. Старые пути не
сохраняются через stubs или дубли. Команды и назначение — в
[справочнике scripts](../../scripts/README.md).

[Полная инвентаризация оставшихся root Python-файлов](root-file-inventory.md)
показывает ответственность каждого файла и границы следующих переносов.
Guardrail запрещает возвращать перенесённые helpers и добавлять shell helpers
в корень. Следующие доменные переносы выполняются отдельными связными PR.

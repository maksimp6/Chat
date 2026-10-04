# Alice Pro Documentation

Это центральная точка входа для документации по проекту Alice Pro. Используйте оглавление ниже для навигации по разделам.

## Разделы документации

- [Архитектура](architecture/overview.md) — общая структура и компоненты системы
- [Канонический AI pipeline](architecture/ai-execution-pipeline.md) — единый путь model/tool execution и правила адаптеров
- [Координация архитектурной интеграции](architecture/integration-coordination.md) — порядок и границы активных потоков работ
- [Async/concurrency runtime contract](runtime/async-concurrency-contract.md) — cancellation, timeout, task/thread boundaries и atomic reservations
- [Текущий интеграционный реестр](integration/current-scope.md) — статус и правила допуска сфокусированных PR
- [Настройка](setup/installation.md) — установка и конфигурация
- [Рабочий процесс разработки](development_workflow.md) — formatter, локальные проверки, CI и fail-closed merge gate
- [Правила именования](development/naming.md) — тексты UI, код фронтенда, файлы, Python-модули, ветки и PR; проверка `tests/test_naming_conventions.py`
- [Облачное окружение Claude Code для Cloud.ru](development/claude-cloud-ops-environment.md) — настройка, setup script, переменные и инструменты (Cloud.ru CLI, EDS, gh, Docker)
- [Production deployment](production-deployment.md) — VPS production runbook; наличие runbook не заменяет post-deploy verification
- [Cloud.ru Container Apps](cloudru-container-apps.md) — v2 baseline serverless deployment runbook; client contract синхронизирован с `master`, live Cloud.ru rollout пока не подтверждён
- [API](api/overview.md) — описание доступных API-интерфейсов
- [MCP](mcp/overview.md) — работа с MCP-серверами и инструментами
- [3D Printing Business](printing3d.md) — shipped-контур заказов, P&L, финансирования и AI-first оценки
- [ChatGPT Apps SDK](mcp/chatgpt_apps.md) — подключение Alice Pro к ChatGPT через MCP
- [Сообщество ботов](integrations/chatgpt-bot-community.md) — интеграция ChatGPT, MCP и A2A-ботов
- [Конспекты внешней документации](notes/README.md) — переработанные знания и выводы для Alice Pro
- [Markdown-зеркала](mirrors/README.md) — близкие к источнику локальные копии внешней документации
- [Markdown-зеркало Cloud.ru](integrations/cloudru-docs-mirror.md) — спецификация crawler/fetch pipeline и manifest для Cloud.ru
- [Агенты](agents/overview.md) — архитектура и взаимодействие AI-агентов
- [Фронтенд](frontend/overview.md) — структура пользовательского интерфейса
- [Бэкенд](backend/overview.md) — архитектура серверной части
- [База данных](database/overview.md) — модели данных и схема БД
- [Безопасность](security/overview.md) — меры защиты и безопасность системы
- [Cloud.ru Secret Management](security/cloudru-secret-management.md) — адаптер и границы прав для секретов Alice Pro
- [Журнал изменений](changelog.md) — история релизов и изменений
- [Синхронизация с bare-репозиторием](bare_sync.md) — процесс зеркалирования изменений в `local_bare`
- [Работа с памятью](memory/overview.md) — управление данными в оперативной памяти

- [Структура репозитория](architecture/repository-layout.md) — правила размещения модулей и план очистки корня

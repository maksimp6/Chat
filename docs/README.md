# Alice Pro Documentation

Это центральная точка входа для документации по проекту Alice Pro. Используйте оглавление ниже для навигации по разделам.

## Разделы документации

- [Архитектура](architecture/overview.md) — общая структура и компоненты системы
- [Канонический AI pipeline](architecture/ai-execution-pipeline.md) — единый путь model/tool execution и правила адаптеров
- [Координация архитектурной интеграции](architecture/integration-coordination.md) — порядок и границы активных потоков работ
- [Текущий интеграционный реестр](integration/current-scope.md) — статус и правила допуска сфокусированных PR
- [Настройка](setup/installation.md) — установка и конфигурация
- [API](api/overview.md) — описание доступных API-интерфейсов
- [MCP](mcp/overview.md) — работа с MCP-серверами и инструментами
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

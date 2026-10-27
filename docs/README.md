# Logos Documentation

Это центральная точка входа для документации по проекту Logos. Используйте оглавление ниже для навигации по разделам.

## Разделы документации

- [Архитектура](architecture/overview.md) — общая структура и компоненты системы
- [Канонический AI pipeline](architecture/ai-execution-pipeline.md) — единый путь model/tool execution и правила адаптеров
- [Координация архитектурной интеграции](architecture/integration-coordination.md) — порядок и границы активных потоков работ
- [Async/concurrency runtime contract](runtime/async-concurrency-contract.md) — cancellation, timeout, task/thread boundaries и atomic reservations
- [Текущий интеграционный реестр](integration/current-scope.md) — действующие контракты и ссылки на задачи без исторической очереди PR
- [Настройка](setup/installation.md) — установка и конфигурация
- [Рабочий процесс разработки](development_workflow.md) — публикация по поручению, проверки только по отдельной команде в runner и доказательства результата
- [Правила надёжного кода](development/reliability.md) — инженерные рекомендации по идемпотентности и структуре Python/JS
- [Правила именования](development/naming.md) — рекомендации для UI, модулей, веток и PR
- [Продакшен-конфиги и секреты деплоя](development/production-deploy-secrets.md) — решение: отдельное защищённое место и хранение секретов в Cloud.ru (не реализовано)
- [Agent shell: стартовый каркас](agents/agent-shell.md) — хранилище задач, исполнитель и первая read-only задача для запуска агентов в своём контейнере
- [Эксплуатация и восстановление](production-deployment.md) — действующий маршрут Redmi 9, сохранность данных и границы восстановления
- [Сервер Redmi 9](redmi-app-server.md) — процесс, launcher, readiness и Wi-Fi
- [Выпуск веб-приложения](release.md) — доставка и критерии результата
- [Cloud.ru Container Apps](cloudru-container-apps.md) — v2 baseline serverless deployment runbook; client contract синхронизирован с `master`, live Cloud.ru rollout пока не подтверждён
- [Keel Platform](platform/architecture.md) — deployment system с config-driven reconciliation
  - [Архитектура](platform/architecture.md) — сервисы, lanes, валидация
  - [Домены](platform/domains.md) — конфигурация маршрутизации
  - [Хранилище](platform/storage.md) — persistent storage и backup
  - [Секреты](platform/secrets.md) — управление credentials
  - [Восстановление](platform/recovery.md) — аварийные процедуры
  - [Деплой](platform/deployment.md) — процесс и gates утверждения
- [API](api/overview.md) — описание доступных API-интерфейсов
- [MCP](mcp/overview.md) — работа с MCP-серверами и инструментами
- [3D Printing Business](printing3d.md) — shipped-контур заказов, P&L, финансирования и AI-first оценки
- [ChatGPT Apps SDK](mcp/chatgpt_apps.md) — подключение Logos к ChatGPT через MCP
- [Сообщество ботов](integrations/chatgpt-bot-community.md) — интеграция ChatGPT, MCP и A2A-ботов
- [Work Map: trips.db](integrations/work-map-trips.md) — ручной прототип обработки `trips.db` и границы будущей интеграции; в Logos не реализовано
- [Конспекты внешней документации](notes/README.md) — переработанные знания и выводы для Logos
- [Официальная документация Yandex AI Studio и Yandex Cloud](yandex-documentation.md) — проверенные ссылки на API, тарифы, лимиты и управление доступом
- [Markdown-зеркала](mirrors/README.md) — близкие к источнику локальные копии внешней документации
- [Markdown-зеркало Cloud.ru](integrations/cloudru-docs-mirror.md) — спецификация crawler/fetch pipeline и manifest для Cloud.ru
- [Агенты Logos](agents/overview.md) — runtime-агенты и адаптеры приложения (чат, голос, CLI, инструменты)
- [Роли разработки в репозитории](agents/role-based-agent-office.md) — GitHub-роли (Team Lead, Backend, Docs Engineer и др.), не часть runtime Logos
- [Процедура Суда Логоса](agents/logos-court-procedure.md) — эпизоды дела #1413, доказательства, независимое ревью и приёмка
- [Фронтенд](frontend/overview.md) — структура пользовательского интерфейса
- [Пробелы пользовательских сценариев](frontend/capability-gaps-2026-10-11.md) — код и локальный UI на 2026-10-11, приоритеты и критерии приёмки
- [Повторно применимые уроки и навыки](lessons/README.md) — источники, границы истории и каталог процедур
- [Бэкенд](backend/overview.md) — архитектура серверной части
- [База данных](database/overview.md) — нативный журнал Kristall и работа с данными
- [Безопасность](security/overview.md) — меры защиты и безопасность системы
- [Cloud.ru Secret Management](security/cloudru-secret-management.md) — адаптер и границы прав для секретов Logos
- [Аудит кода 2026-10-26](audits/2026-10-26-code-defects/README.md) — дефекты по чтению кода, метод поиска и критерии исправления (часть по безопасности не публикуется)
- [Неудобства интерфейса 2026-10-26](audits/2026-10-26-ux-friction/README.md) — пункты, которые мешают пользователю, метод поиска и критерии исправления
- [Журнал изменений](changelog.md) — история релизов и изменений
- [Синхронизация с bare-репозиторием](bare_sync.md) — процесс зеркалирования изменений в `local_bare`
- [Работа с памятью](memory/overview.md) — управление данными в оперативной памяти

- [Структура репозитория](architecture/repository-layout.md) — правила размещения модулей и план очистки корня

- [Инвентаризация файлов корня](architecture/root-file-inventory.md) — ответственность всех оставшихся root Python-файлов
- [Служебные скрипты](../scripts/README.md) — команды диагностики и разработки

# Документация Logos

Указатель документации проекта Logos. Документы сгруппированы по областям,
у каждой ссылки — одна строка о содержании.

Правила работы в репозитории задают [AGENTS.md](../AGENTS.md) и
[CONTRIBUTING.md](../CONTRIBUTING.md). Обзор проекта — в [корневом README](../README.md).

## С чего начать

| Задача | Документ |
| --- | --- |
| Установить и запустить Logos | [Установка](setup/installation.md), затем [запуск в режиме разработки](setup/development.md) |
| Понять путь запроса к модели и инструментам | [Канонический AI pipeline](architecture/ai-execution-pipeline.md) |
| Внести и опубликовать изменение | [Рабочий процесс разработки](development_workflow.md) |
| Обновить или восстановить приложение на Redmi 9 | [Эксплуатация и восстановление](production-deployment.md) |
| Найти повторно применимую процедуру | [Уроки и навыки](lessons/README.md) |

**Разделы:** [Начало работы](#начало-работы) · [Процесс разработки](#процесс-разработки) ·
[Архитектура](#архитектура) · [Runtime](#runtime) · [API и контракты](#api-и-контракты) ·
[MCP и инструменты](#mcp-и-инструменты) · [Модели и провайдеры](#модели-и-провайдеры) ·
[Агенты](#агенты) · [Память и данные](#память-и-данные) · [Фронтенд](#фронтенд) ·
[Бэкенд и биллинг](#бэкенд-и-биллинг) · [Безопасность](#безопасность) ·
[Эксплуатация и выпуск](#эксплуатация-и-выпуск) · [Redmi 9](#redmi-9) ·
[Keel Platform](#keel-platform) · [Интеграции и облако](#интеграции-и-облако) ·
[3D-печать](#3d-печать) · [Аудиты](#аудиты) ·
[Планы и исторические материалы](#планы-и-исторические-материалы)

## Начало работы

- [Установка](setup/installation.md) — системные требования, установка и конфигурация; Android/Termux — один из вариантов запуска
- [Запуск в режиме разработки](setup/development.md) — backend, CLI/Termux и навигация для разработчика
- [P0 local launch smoke](setup/launch-smoke.md) — исторический документ: скрипт и workflow проверки из issue #427 удалены
- [Cloud.ru и второй Android-телефон](setup/cloudru-local-agent.md) — Logos за HTTPS reverse proxy и подключение телефона
- [Служебные скрипты](../scripts/README.md) — команды диагностики и разработки

## Процесс разработки

- [Рабочий процесс разработки](development_workflow.md) — публикация по поручению, проверки только по отдельной команде в runner и доказательства результата
- [Интеграция в develop](development/develop-integration.md) — `develop` как интеграционная ветка, сверка HEAD/base и фактических результатов проверок
- [Текущий интеграционный реестр](integration/current-scope.md) — действующие контракты и ссылки на задачи без исторической очереди PR
- [Координация архитектурной интеграции](architecture/integration-coordination.md) — порядок и границы активных потоков работ
- [Правила надёжного кода](development/reliability.md) — инженерные рекомендации по идемпотентности и структуре Python/JS
- [Правила именования](development/naming.md) — рекомендации для UI, модулей, веток и PR
- [Sprint workflow](development/sprint-workflow.md) — короткие проверяемые итерации с вертикальным приращением
- [Обновления зависимостей](development/dependency-updates.md) — политика Dependabot по экосистемам и каталогам
- [Повторно применимые уроки и навыки](lessons/README.md) — источники, границы истории и каталог процедур
- [Инженерные уроки](lessons/engineering-lessons.md) — границы продукта и исполнения, настройки и доказательства (H01–H18)
- [Журнал изменений](changelog.md) — история релизов и изменений

## Архитектура

- [Канонический AI pipeline](architecture/ai-execution-pipeline.md) — единый путь model/tool execution и правила адаптеров
- [Execution Trace и расчёт стоимости](architecture/execution_trace.md) — один `ExecutionTrace` на логическую операцию запроса
- [Cloud Gateway и Local Tool Agent](architecture/local_tool_agent.md) — разделение центрального Gateway и локального исполнителя
- [SSH Runtime](architecture/ssh_runtime.md) — команды и файловые операции на удалённых Linux-хостах через OpenSSH
- [Общая память агентов](architecture/agent-memory.md) — GitHub-first хранилище памяти агентов репозитория (issue #578)
- [Индекс ревизий репозитория](architecture/repository-revision-index.md) — неизменяемый Python-индекс в `scripts/build_ai_index.py` (#538)
- [Branch Environments: architecture review](architecture/branch-environments.md) — design review до реализации (#13, PR #330)
- [ChatGPT Browser Tool](architecture/chatgpt-browser-tool.md) — граница внешнего инструмента для persistent Chrome Worker
- [Проверки Redmi 9](architecture/browser-self-testing.md) — проверки на реальном Redmi 9, оставшиеся после удаления общих тестов

## Runtime

- [Async/concurrency runtime contract](runtime/async-concurrency-contract.md) — cancellation, timeout, task/thread boundaries и atomic reservations
- [Runtime dispatcher policy](runtime/runtime-dispatcher-policy.md) — изоляция preview runtimes внутри одного Python-процесса
- [Threaded environment host](runtime/threaded-environment-host.md) — окружения приложения как управляемые потоки с неизменяемым `RuntimeContext`
- [Настройки SSH Runtime](runtime/ssh-runtime.md) — серверная структурированная конфигурация SSH Runtime
- [Branch-aware environments](branch-environments.md) — окружение ветки как неизменяемый runtime одного коммита
- [Host-managed preview validation](preview-deployments.md) — проверка preview на работающем хосте без отдельного контейнера
- [AI contract: preview runtime isolation](agents/runtime-dispatcher-contract.md) — инструкция для AI-агентов, меняющих preview runtimes и маршрутизацию

## API и контракты

- [API](api/API_DOCS.md) — реализованные endpoints, начиная с `POST /api/chat`
- [API Contracts](api/contracts.md) — каждая граница API как явный контракт
- [Ошибки API (RFC 9457)](api/errors.md) — формат Problem Details
- [Контракты Yandex API](../contracts/yandex/README.md) — машинные контракты всех API-страниц снимка AI Studio и их проверка
- [Исходящие запросы через контракты](../contracts/yandex/transport.md) — контрактная граница исходящих вызовов Yandex

## MCP и инструменты

- [Архитектура MCP и Tool Calling](mcp/architecture.md) — единый реестр инструментов и меры безопасности
- [Доступные MCP-инструменты](mcp/tools.md) — справочник категорий по реестру `tool_providers/registry.py`
- [ChatGPT Apps SDK](mcp/chatgpt_apps.md) — подключение Logos к ChatGPT через MCP
- [Alice Pro MCP control endpoint](mcp-alice-pro.md) — control plane Alice Pro через существующий `/mcp`
- [Plugin System](plugins.md) — локальные версионированные расширения из `LOGOS_PLUGIN_DIR`

## Модели и провайдеры

- [Модели Yandex AI Studio](models/yandex-models.md) — каталог моделей Logos, сверенный с документацией Яндекса
- [Dynamic model discovery](models/model-discovery.md) — список моделей из endpoint `/models` и его кеширование
- [Yandex embeddings](models/embeddings.md) — `POST /api/embeddings` через OpenAI-совместимый endpoint Яндекса
- [Yandex classifiers](models/classification.md) — `POST /api/classify` через Text Classification API
- [Ротация ключей провайдеров](provider-key-rotation.md) — жизненный цикл backend-owned API-ключа провайдера
- [Квоты провайдеров](provider-quotas.md) — прежние per-user квоты запросов к Yandex Responses API; сейчас квоты отключены
- [Голосовой ассистент](voice-assistant.md) — Yandex Realtime и цепочка SpeechKit STT → текстовая модель → TTS
- [Профили Optima](agents/logos-optima-model-profiles.md) — сбалансированный пресет для существующих моделей Yandex Responses
- [Профиль Pro 5.1 async](agents/logos-pro51-async-profile.md) — выбранный владельцем YandexGPT Pro 5.1 в асинхронном API
- [Ultra-профили моделей Logos](agents/logos-ultra-model-profiles.md) — 14 профилей локального реестра
- [Ultra-профили всех моделей Яндекса](agents/logos-yandex-ultra-model-profiles.md) — каталог по моделям Yandex AI Studio и SpeechKit

## Агенты

- [Агенты Logos](agents/overview.md) — runtime-агенты и адаптеры приложения (чат, голос, CLI, инструменты)
- [Роли разработки в репозитории](agents/role-based-agent-office.md) — GitHub-роли (Team Lead, Backend, Docs Engineer и др.), не часть runtime Logos
- [Процедура Суда Логоса](agents/logos-court-procedure.md) — эпизоды дела #1413, доказательства, независимое ревью и приёмка
- [Native Logos: качество и контекст дела](agents/logos-native-quality.md) — текущие рамки работы и полный контекст дела
- [Agent shell: стартовый каркас](agents/agent-shell.md) — хранилище задач, исполнитель и первая read-only задача для запуска агентов в своём контейнере
- [Logos как GitHub-агент](agents/github-agent.md) — работа Logos над GitHub issue в собственном коде
- [Рассказ Логоса о запуске](agents/logos-runner-narration.md) — рассказ по сохранённому `result.json` раннера без вызова модели
- [EDS runner](agents/eds-runner.md) — установка официального EDS v0.4.0 с проверкой SHA-256
- [Departments](agents/departments.md) — доменные контейнеры агента, инструментов и политик с REST API
- [Partner Relations](agents/partner-relations.md) — отдел с owner-scoped партнёрами, контактами и follow-up
- [Взаимодействие агентов](agents/communication.md) — обмен сообщениями, координация и контекст
- [Agent observer](agents/agent-observer.md) — наблюдение за работой агентов в issues и pull requests
- [Process observation and governance](agents/process-observation-and-governance.md) — наблюдение и улучшение процесса, когда обычный цикл не сходится
- [Agent Rules Registry](agents/agent-rules-registry.md) — кандидат v1: ошибка → доказательство → правило (#1159)
- [Репозиторий в RAG](agents/repository-rag.md) — полный репозиторий как доказательная база существующего RAG (#1413)
- [Поиск по истории задач](agents/semantic-pipeline.md) — семантические цитаты из задач (#1413, PR #1435)

## Память и данные

- [Хранилище данных](database.md) — нативный `kristall.Kristall` с append-only журналом; SQL-бэкенда нет
- [Memory DB v1](memory/core-contract.md) — публичный API `MemoryStore` и семантика журнала
- [Kristall v1: durability и миграция](memory/memory-db-v1-contract.md) — контракт реализации issue #776
- [Synchronous Kristall](memory/synchronous-store.md) — функциональное ядро и адаптер задач Logos (#1045, #1046)
- [Replication receiver](memory/replication-receiver.md) — необязательный приёмник репликации с HMAC-аутентификацией
- [Memory DB: границы](memory/MEMORY_DB_BOUNDARIES.md) — владение и допустимые зависимости `agent_memory → memory_engine`
- [Memory DB Core](memory/MEMORY_DB_CORE_CONTRACT.md) — локальный функциональный контракт минимального API
- [Memory DB: решения ревью](memory/MEMORY_DB_REVIEW_DECISIONS.md) — production-клиенты используют только `MemoryStore`
- [Logos Board](project-board.md) — локальная альтернатива GitHub Discussions/Issues на Memory DB, изолированный MVP

## Фронтенд

- [Структура интерфейса](frontend/overview.md) — структура пользовательского интерфейса; ресурсы загружаются только по локальным путям
- [Execution Surface](frontend/execution-surface.md) — read-only проекция ExecutionTrace `logos.execution_surface.v1`
- [Стандарт модулей v1](frontend/module-standard.md) — C-подобная граница API фронтенд-модулей
- [Политика исходников модулей](frontend/module-policy.md) — проверка исходника до выполнения модуля
- [Critical bundle budget](frontend/critical-bundle-budget.md) — методика и замер: помещается ли минимальная рабочая версия в 50 KB (#223)
- [Progressive enhancement audit](frontend/progressive-enhancement-audit.md) — исходный аудит перед следующим срезом (#227)
- [AI theme assistant](frontend/theme-assistant.md) — локальный инструмент `set_ui_theme`
- [Пробелы пользовательских сценариев](frontend/capability-gaps-2026-10-11.md) — код и локальный UI на 2026-10-11, приоритеты и критерии приёмки

## Бэкенд и биллинг

- [Логирование и диагностика](backend/logging.md) — Execution Trace как основа диагностики запросов
- [Anonymous user bootstrap](backend/anonymous-user-bootstrap.md) — прежняя регистрация установки; `POST /api/users/bootstrap` сейчас только совместимый endpoint без регистрации и токена
- [Энергетический биллинг вычислений](compute-energy-billing.md) — перевод локальных вычислений из ExecutionTrace в энергию и деньги
- [Treasury billing](treasury-billing-followup.md) — связь биллинга ExecutionTrace с Paper Balance (issue #12)

## Безопасность

- [Shared local access](security/github-login.md) — текущий режим доступа (обновлён 2026-10-24)
- [Менеджер криптографии и ключей](security/key_manager.md) — единая граница управления секретами и их шифрованное хранение
- [Граница local-files](security/local-files-confinement.md) — `GET /api/local-files` только внутри настроенного корня
- [Cloud.ru Secret Management](security/cloudru-secret-management.md) — адаптер и границы прав для секретов Logos
- [Issue #650: owner unlock protocol](security/issue-650-owner-unlock-protocol.md) — только дизайн, без разблокировки устройства

## Эксплуатация и выпуск

- [Эксплуатация и восстановление](production-deployment.md) — действующий маршрут Redmi 9, сохранность данных и границы восстановления
- [Выпуск веб-приложения](release.md) — доставка и критерии результата
- [Cloud.ru Container Apps](cloudru-container-apps.md) — v2 baseline serverless deployment runbook; client contract синхронизирован с `master`, live Cloud.ru rollout пока не подтверждён
- [Бюджет внешних RDC-вызовов](operations/rdc-call-budget.md) — бюджет внешнего коннектора RDC (#650, #1131)
- [GitHub Pages](deployment/printer-pages.md) — подготовка страницы проекта; сайт не опубликован
- [Синхронизация с bare-репозиторием](bare_sync.md) — процесс зеркалирования изменений в `local_bare`

## Redmi 9

- [Сервер Redmi 9](redmi-app-server.md) — процесс, launcher, readiness и Wi-Fi
- [Обновления через GitHub runner](setup/redmi9-auto-update.md) — runner `redmi9-python` как единственный автоматический путь обновления
- [Termux shell runner](redmi-python-runner.md) — задания GitHub Actions прямо в Termux на Redmi 9
- [RootAgent CLI](redmi-root-agent.md) — проверка root, снимок экрана и ввод мыши из задания runner
- [Android diagnostics](redmi-android-diagnostics.md) — сбор logcat и диагностики в Termux
- [Скриншоты UI](redmi-screenshots.md) — автоматические снимки интерфейса через workflow сервера
- [Ключ модели от runner](redmi-model-key-issuance.md) — выпуск API-ключа Yandex Cloud процессом runner
- [Архив репозитория](redmi-repository-backup.md) — резервная копия репозитория из shell-задания Termux
- [RootAgent: приёмка на устройстве (#650)](testing/rootagent-redmi9-650.md) — аппаратные наблюдения от 2026-10-13

## Keel Platform

Deployment system с config-driven reconciliation.

- [Архитектура](platform/architecture.md) — сервисы, lanes, валидация
- [Домены](platform/domains.md) — конфигурация маршрутизации
- [Хранилище](platform/storage.md) — persistent storage и backup
- [Секреты](platform/secrets.md) — управление credentials
- [Восстановление](platform/recovery.md) — аварийные процедуры
- [Деплой](platform/deployment.md) — процесс и gates утверждения
- [Alice Cloud MVP](platform/alice-cloud.md) — собственный control plane инфраструктуры Alice Pro на Docker

## Интеграции и облако

- [Сообщество ботов](integrations/chatgpt-bot-community.md) — интеграция ChatGPT, MCP и A2A-ботов
- [Cloud provider foundation](integrations/cloud-provider-foundation.md) — provider-neutral облачные инструменты с реализацией Cloud.ru (issue #417)
- [Cloud.ru Object Storage](integrations/cloudru-object-storage.md) — адаптер `StorageProvider` поверх S3 API (issue #443)
- [Markdown-зеркало Cloud.ru](integrations/cloudru-docs-mirror.md) — спецификация crawler/fetch pipeline и manifest для Cloud.ru; статус not_planned
- [Cloud.ru provider connector](tasks/issue-417-cloudru-provider.md) — постановка задачи для issue #417

## 3D-печать

- [Конвейер Bambu Lab A1 mini](printing3d-a1mini-full-workflow.md) — от выбора модели до печати и видео (#1096); в production не развёрнут
- [FDM shape capability protocol](printing3d/shape-qa.md) — размерный benchmark и правило нулевых отходов (#1152)
- [3D-превью в комментариях GitHub](printing3d-render-comments.md) — автоматические превью STL/3MF для PR (#1118)
- [3D Printing Universal / MCP tools](mcp/printing3d-tools.md) — инструменты категории `3d`, owner identity и approval
- [Local 3D Model Editor](mcp/model-editor.md) — редактируемые сцены и экспорт STL (MVP)
- [Visual recognition benchmark](development/visual-object-recognition.md) — бенчмарк визуального распознавания 3D-рендеров (PR #1146)

## Аудиты

- [Аудит кода 2026-10-26](audits/2026-10-26-code-defects/README.md) — дефекты по чтению кода, метод поиска и критерии исправления (часть по безопасности не публикуется)
- [Неудобства интерфейса 2026-10-26](audits/2026-10-26-ux-friction/README.md) — пункты, которые мешают пользователю, метод поиска и критерии исправления
- [UI-аудит Redmi 9, 2026-10-16](audits/2026-10-16-redmi9-ui/README.md) — исследование живого UI по скриншотам; статус PARTIAL / BLOCKED
- [PR #1621: интеграция](audits/pr-1621-integration.md) — решения по интеграции и конфликтам с runner-only архитектурой
- [PR #1621: регрессии на Redmi 9](audits/pr-1621-validation/README.md) — офлайн-регрессии на физическом runner
- [Alice Pro: независимое ревью](audits/Alice_Pro_Review_50000_words.md) — READ-ONLY ревью обязательств, архитектуры, Issues, PR и CI на 11 октября 2026 года

## Планы и исторические материалы

- [Issue #75: план реализации](issue_75_implementation_plan.md) — этапы, которые можно ревьюить независимо
- [Единый Execution Trace](tasks/execution-trace.md) — постановка задачи трассировки `/api/chat`
- [Вопрос 14](simulations/q14.md) — воспроизводимый учебный стенд
- [Video evidence для browser tool](testing/browser-tool-video-evidence.md) — спецификация сценариев; их запуск не заявлен
- [CI cache experiment](ci/cache-experiment.md) — эксперимент с кешем окружения CI (#757 / #762)
- [Удаление PostgreSQL из тестов](development/postgresql-test-removal.md) — чек-лист, удаление не завершено
- [Alice Pro: подготовка PyPI-релиза](development/pypi-release.md) — статус NOT READY FOR PUBLICATION
- [Logos Dev: OAuth origin](development/dev-oauth-acceptance.md) — публичный OAuth origin и проверка подключения (#939, #941)
- [Подтверждение доступа к приложению](development/production-public-acceptance.md) — что фиксировать при порученной проверке доступа
- [Live Server input composition](development/live-server-input-compose-1161.md) — сборка staging-веток ввода, только staging (#1161)

---

Обзоры, на которые раньше ссылался этот указатель (`architecture/overview.md`,
`api/overview.md`, `mcp/overview.md`, `backend/overview.md`, `security/overview.md`,
`memory/overview.md`, `printing3d.md` и другие), удалены из дерева; их последние
версии доступны в истории Git.

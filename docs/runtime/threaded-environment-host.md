# Потоковый host окружений

Окружения приложения Alice Pro выполняются внутри одного Python-процесса.
Работающее окружение имеет управляемый рабочий поток и собственный
`RuntimeContext`. База описания указана в
[интеграционном реестре](../integration/current-scope.md).

## Путь HTTP-запроса

```text
HTTP -> environment gateway -> проверка owner/runtime
    -> очередь рабочего потока -> RuntimeDispatcher -> операция
    -> ограниченная очередь ответа -> HTTP response
```

Gateway не обращается к localhost-порту другого Flask-процесса. Ограниченная
очередь ответа обеспечивает backpressure, чтобы медленный клиент не требовал
полной буферизации ответа в памяти. Контракт включает streaming, отмену и очистку.

## Scope и данные

В диспетчере регистрируются `runtime_id`, `owner_id`, namespace и собственный
корневой каталог данных. Общие ресурсы доступны только через
[RuntimeDispatcher](runtime-dispatcher-policy.md); прямой доступ к данным другого
runtime не является допустимым обходом.

Runtime root переносится через request-local `ContextVar`.
`db.get_conn()` выбирает `<runtime-data-root>/alice_pro.db`, а не базу host.
Это правило действует и при PostgreSQL или in-memory backend на host.
Схема runtime SQLite подготавливается до начала работы потока. База control plane
остаётся отдельно от runtime scope.

Runtime URL-prefix также переносится request-local. Не меняйте
`ALICE_PREVIEW_BASE_PATH` в общем окружении процесса для переключения одного
runtime: это не механизм изоляции параллельных запросов.

## Жизненный цикл

`EnvironmentManager` хранит постоянные записи окружений. Legacy-поля
`runtime_pid` и `runtime_port` не используются как адрес потокового runtime;
его работа отражается через `runtime_thread_id`.

Остановка отменяет активные потоки ответа, завершает worker и снимает runtime
с регистрации в диспетчере. Удаление дополнительно очищает detached worktree
и собственный каталог данных. Ошибки и неполное завершение очистки должны
оставаться видимыми, а не считаться успешным удалением только по запросу API.

## Загрузка ревизии

Менеджер разрешает ref в точный commit и создаёт detached worktree. Несоответствие
SHA или грязная рабочая копия не должны допускаться к запуску.
`RuntimeLoader` загружает ограниченную точку входа `alice_runtime.py` в отдельное
пространство имён без изменения общих `sys.path` или `sys.modules`.

Точка входа определяет `create_runtime(host)` и возвращает объект с
`invoke(operation, payload)`. В этой реализации импорты, включая динамические,
ограничены; shared-ресурсы доступны через host capabilities и диспетчер.
Вызов ревизии использует операцию `revision.invoke`.

Потоки разделяют память интерпретатора. Этот контракт предотвращает случайное
смешение состояния, но не защищает от враждебного Python, reflection,
monkey-patching, native extensions и исчерпания ресурсов. Недоверенный код
нельзя считать безопасным для загрузки в этот процесс.

## Ограничение HTTP-приложения

В [environment_routes.py](../../environment_routes.py) HTTP-операция использует
приложение host через `app.test_client()`. Загрузка `alice_runtime.py` не заменяет
весь Flask HTTP/asset-путь кодом выбранной ветки.

Поэтому различайте: проверку exact-commit worktree, выполнение ограниченной
revision-операции и полноценное обслуживание HTML/API/assets выбранной ревизией.
Последнее нельзя объявлять реализованным по одному `healthz` или записи SHA.

## Preview workflow и развёртывание

Host-managed workflow вошёл в `master` через
[#359](https://github.com/maksimp6/Chat/pull/359); runtime-фундамент #347/#349
вошёл через [#363](https://github.com/maksimp6/Chat/pull/363).
Это уже не очередь ожидающих реализации foundation PR.

Развёртывание совместимого host, аутентификация и фактический запуск preview
проверяются отдельно. Workflow остаётся ограничен
`ALICE_HOST_PREVIEW_ENABLED`. Пропущенная проверка не означает успешного деплоя.
Точные параметры и пределы доказательства описаны в
[preview-документе](../preview-deployments.md).

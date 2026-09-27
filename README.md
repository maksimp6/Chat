# Alice Pro

Alice Pro предназначена для самостоятельного размещения AI-ассистента на базе
Yandex AI Studio. В проект входят веб-чат, local/MCP-инструменты, агенты,
Execution Trace, работа с файлами, внутренний биллинг и Android WebView-клиент.

**[Документация](docs/README.md) · [Архитектура](docs/architecture/overview.md) ·
[Состояние интеграции](docs/integration/current-scope.md)**

Проект развивается. Наличие модуля или слитого PR не означает готовность всей
функции к production. Подтверждённые изменения, открытые задачи и известные
блокеры отделены друг от друга в интеграционном реестре.

## Компоненты

- Чат с Yandex AI Studio Responses API и циклом инструментов.
- `UniversalToolExecutor` для общего исполнения local/MCP-вызовов.
- `InvocationContext` и `ExecutionTrace` для корреляции запросов, polling,
  инструментов, ошибок, времени и стоимости.
- Agent Gateway, управляемые runtime-окружения и отдельный опциональный SSH Runtime.
- Файловый менеджер, память, знания, департаменты и основы агентной маршрутизации.
- Казначейство и биллинг для внутреннего учёта использования.
- SQLite по умолчанию; PostgreSQL включается явно через `ALICE_DATABASE_URL`.
- Опциональный Supabase trace mirror для диагностики.
- Android-клиент с WebView, диагностикой и механизмами обновления.

## Архитектура

```text
Клиент -> Flask -> InvocationContext + ExecutionTrace
    -> Yandex Responses API
    -> UniversalToolExecutor -> local/MCP-инструменты
    -> ответ и расчёт стоимости

Окружения веток:
EnvironmentManager -> RuntimeLoader -> RuntimeDispatcher
    -> управляемые потоки и собственные данные внутри одного процесса
```

Runtime-окружение не является отдельным Flask-сервером или контейнером на каждое
preview. Потоки не являются защитной песочницей для недоверенного Python.
Текущий gateway использует HTTP-приложение host; наличие worktree выбранного
коммита ещё не доказывает обслуживание всего интерфейса кодом этой ветки.
Подробности и границы: [обзор архитектуры](docs/architecture/overview.md).

## Состояние проекта

Документированный срез от 27 сентября 2026 года относится к `master`
`de8f97c8342c8c073c84885bf8f301bba5692b33`. На этой базе были зафиксированы сбои CI
и Android-сборки. Поэтому README не обещает зелёный pipeline или готовый APK.
Точные run, причины и незавершённые PR приведены в
[реестре](docs/integration/current-scope.md).

Новый контракт `make_snapshot()` / `finalize()` пока описан как требование #401,
а не готовая возможность текущего `master`. Локальный storage-адаптер не
равнозначен подключённому Google Drive. Согласованные возможности и проверенный
production-деплой не следует смешивать.

## Локальный запуск backend

Выполняйте команды из корня репозитория. Для пути, соответствующего backend CI,
используется Python 3.12. Версии и команды проверок задаются исходниками и
[workflow CI](.github/workflows/ci.yml).

```bash
python3 -m venv venv
source venv/bin/activate
python -m pip install -r requirements.txt
test -f .env || cp .env.example .env
```

Заполните локальный `.env` по [.env.example](.env.example). Для провайдера нужны
свои `YANDEX_API_KEY` и `YANDEX_PROJECT_ID`; также настройте `SECRET_KEY` и
параметры идентичности владельца согласно выбранному режиму. Не публикуйте
значения этих переменных.

Для запуска только на локальном интерфейсе:

```bash
HOST=127.0.0.1 PORT=8080 python app.py
```

Откройте `http://127.0.0.1:8080`. Доступ к публичному host требует отдельной
настройки аутентификации, сети и секретов; локальный запуск не является
инструкцией безопасного production-развёртывания.

SQLite используется по умолчанию. PostgreSQL выбирается через
`ALICE_DATABASE_URL`; подробности в [описании БД](docs/database.md).
Supabase mirror настраивается отдельно на backend и не заменяет основную БД.
См. [миграции и настройку Supabase](docs/supabase-migrations-deploy.md).

## Android

Исходники [Android-модуля](android/) задают Java 17, `compileSdk 37`,
`targetSdk 35`, `minSdk 26` и встроенный Python 3.13. В проверенной ревизии нет
`android/gradlew`; CI использует установленный Gradle 9.5.0. Команда с
несуществующим wrapper не является рабочей инструкцией.

При подготовленном Android SDK, Python и Gradle:

```bash
cd android
python scripts/stage_python.py
: "${ALICE_BUILD_NUMBER:?Задайте номер сборки для versionCode}"
gradle --no-daemon \
  -PaliceBuildNumber="$ALICE_BUILD_NUMBER" \
  -PaliceCommitHash="$(git rev-parse HEAD)" \
  :app:testDebugUnitTest :app:assembleDebug
```

Номер сборки должен соответствовать политике обновления установленного APK.
Для обновления поверх предыдущей установки важны также package и сертификат
подписи. Debug- и release-ключи нельзя смешивать, коммитить или публиковать в
артефактах. [Android README](android/README.md) содержит дополнительные детали;
фактический успех сборки подтверждается CI и проверкой артефакта.

## Разработка и проверки

```text
Issue -> ветка от свежего master -> изменение -> проверки -> PR
    -> CI и review -> принятие -> слияние -> проверка результата
```

Используйте [AGENTS.md](AGENTS.md), [CONTRIBUTING.md](CONTRIBUTING.md) и
[спринтовый процесс](docs/development/sprint-workflow.md). Не считайте локальный
коммит или подготовленное описание PR опубликованным результатом.

Проверки backend из корня репозитория при установленных тестовых зависимостях:

```bash
python -m compileall -q .
python tests/validate_runtime_modules.py
pytest -q
```

Для frontend используются BrowserShim/VM и HTTP-контракты. Для изменений схемы
проверяются SQLite и PostgreSQL. Изменения только документации проверяются по
ссылкам, командам и форматированию; обязательный CI не отключается.

## Конфигурация и безопасность

Секреты находятся в локальном `.env` или защищённом хранилище среды исполнения.
Нельзя помещать в Git, issue, PR, trace или логи API-ключи, IAM/bearer-токены,
пароли, cookies, SSH/GPG-ключи и Android keystore.

Ключи провайдеров обрабатываются на backend. Документ о
[ротации ключей](docs/provider-key-rotation.md) описывает отдельную подсистему;
он не даёт клиенту права выбирать доверенного владельца биллинга.

Сообщения об уязвимостях направляйте по [SECURITY.md](SECURITY.md).

## Диагностика

**Ошибка Yandex 401/403:** сверяйте URI модели, права проекта и конфигурацию
backend. Сохраняйте безопасную исходную причину в trace, не публикуя ключ.

**Ошибка таблицы или mirror Supabase:** проверяйте миграции и соответствующий run,
не выполняйте разрушительный rollback ради устранения сообщения.

**Конфликт установки APK:** проверяйте application ID `com.alicepro.mobile`,
`versionCode` и сертификат подписи. Ошибки перекрытия интерфейса системными
панелями проверяйте на целевом устройстве, не только в тестовом эмуляторе DOM.

**В trace нет инструмента:** проверяйте `tool_calls`, события, ошибки и продолжения
операции. Текст ответа ассистента не является доказательством выполнения.
Подробнее: [Execution Trace и биллинг](docs/architecture/execution_trace.md).

## Документация и дальнейшая работа

[Центральный указатель](docs/README.md) ведёт к API, MCP, runtime, frontend,
агентам, БД и правилам сопровождения. GitHub Issues остаются источником
требований и критериев готовности. Реестр отделяет вошедшие foundation-изменения
от оставшейся работы над агентами, облачным хранилищем, preview, биллингом и
публичными Android-релизами.

[Поддержка](SUPPORT.md) · [Участие](CONTRIBUTING.md) ·
[Кодекс поведения](CODE_OF_CONDUCT.md)

## Лицензия

MIT. См. [LICENSE](LICENSE).

# EDS: точная памятка для Codex

Проверено 2026-09-28 по исходникам официального релиза
[v0.4.0](https://github.com/cloud-ru/evolution-devservices-cli/tree/v0.4.0),
commit `b75066263c156695b30ec3d6ed627a8fc6c5548c`.
EDS означает Evolution DevServices CLI. Поддерживает Repo и Workflow Studio.

## Установка

Из корня Alice Pro:

```bash
python scripts/install_eds.py
export PATH="$HOME/.local/bin:$PATH"
eds version
eds --help
eds wf app create --help
```

Installer фиксирует v0.4.0, проверяет SHA-256 release asset **до исполнения**,
поддерживает Linux/macOS amd64/arm64, не требует sudo и не запускает install.sh
из изменяемого `main`. Обновление версии — отдельное проверяемое изменение.

## Конфигурация и ключи

Использовать уже авторизованные `EDS_API_KEY` и `EDS_PROJECT_ID`, внедрённые
через секреты среды запуска. Ключ — продуктовый DevServices `X-API-KEY`, не
пара IAM Key ID/Key Secret и не Foundation Models API key.

| Переменная | Значение по умолчанию / назначение |
|---|---|
| `EDS_CONFIG` | Файл вместо `~/.config/eds/config.json`; учитывается `XDG_CONFIG_HOME` |
| `EDS_PROJECT_ID` | UUID проекта; установить явно |
| `EDS_API_KEY` | Секрет DevServices |
| `EDS_REPO_API_URL` | `https://devtools.api.cloud.ru/repo/api/v1` |
| `EDS_REPO_GIT_HOST` | `https://repo.cloud.ru/` |
| `EDS_WF_API_URL` | `https://pipeline.cloud.ru/public-api/v1` |

Приоритет: defaults → JSON-файл → environment → flags. Имена полей JSON:
`api_url`, `git_host`, `workflow_api_url`, `project_id`, `api_key`. Шаблон
`deploy/cloudru/eds.config.example.json` намеренно не содержит `api_key`.
Не направлять секреты на произвольные endpoint overrides.

Если нужна именно запись локального login, EDS поддерживает
`eds login --stdin --project "$EDS_PROJECT_ID"`; подавать ключ из защищённого
источника через stdin при отключённом shell tracing. После записи проверить
права файла 0600 и каталога 0700, в том числе для уже существовавших файлов.
Для CI достаточно environment, сохранять ключ повторно не требуется.
Не использовать `--api-key VALUE`, `printenv`, `set -x`, `cat config.json`.
Даже `eds config` показывает первые/последние символы ключа.

## Чтение

Команды ниже — справочник, их raw stdout/stderr **не публикуется** в агентский
чат, issue или CI artifact. Capture internally, затем вывести allowlist полей
`id`, `name`, `status`, `default_branch`, проверив тип и содержание значений.
`show` может возвращать environment и другие чувствительные поля.

```bash
eds repo list --sort updated_at_desc --limit 50 --offset 0 --json
eds repo show "$EDS_REPOSITORY_ID" --json
eds wf app list --sort created_at_desc --limit 50 --offset 0 --json
eds wf app show "$EDS_APPLICATION_ID" --json
eds wf app deployments "$EDS_APPLICATION_ID" --limit 50 --offset 0 --json
eds wf run show "$EDS_RUN_ID" --json
```

Продолжить `offset` по страницам до исчерпания; проверить дубликаты и total,
если API его сообщает. Неполную выдачу не объявлять отсутствием ресурсов.
Не угадывать ID по имени. `repo show` принимает ID/имя; workflow принимает ID.
У job logs есть `--limit` и `--before`, но содержимое не является безопасным
отчётом. Не загружать raw logs в issue.

## Изменения: после заполнения deployment runbook

```bash
# МУТАЦИЯ: создаёт Repo; никакого автоматического зеркалирования GitHub.
eds repo create alice-pro --visibility private --json

# МУТАЦИЯ И НЕМЕДЛЕННЫЙ ДЕПЛОЙ: применять только к подготовленной цели.
eds wf app create alice-pro --repository-url https://github.com/maksimp6/Chat.git --branch master --json
# Вместо --repository-url можно использовать --repository "$EDS_REPOSITORY_ID".

# МУТАЦИЯ: меняет источник существующего приложения.
eds wf app update "$EDS_APPLICATION_ID" --branch master

# МУТАЦИЯ И ДЕПЛОЙ: запускает pipeline configured branch.
eds wf app deploy "$EDS_APPLICATION_ID" --json
```

Не исполнять этот блок целиком. Для существующего приложения create не нужен.
Публичный GitHub URL подходит только если сервис действительно может читать
репозиторий. Для private source нужны подтверждённые credentials/integration;
не добавлять токен в URL. Canonical source остаётся `maksimp6/Chat:master`.

В v0.4.0 `app create` по умолчанию использует `main`, поэтому указываем `master`.
CLI не имеет флагов установки runtime secrets, CPU, volumes, network, logging
или immutable commit/digest. Нельзя писать вымышленные `eds container`,
`eds postgres`, `eds secret`, `--env`, `--image` или `--commit`.
Конфиги в `deploy/cloudru` не загружаются командой `eds apply`: такой команды нет.

Для production pipeline обязан сам проверить утверждённый SHA master, собрать
его точный `git archive`, записать digest и применить нужную runtime-конфигурацию
**до публикации**. Если Workflow Studio этого не позволяет, его create/deploy
не считается готовым production-путём: использовать проверенный Container Apps
API flow после подготовки его зависимостей. Не менять старое работающее
приложение ради обхода этого ограничения.

`eds wf run stop` прерывает run, но не откатывает уже опубликованный образ.
Удаление приложения удаляет связанные deployments; это не rollback.

## Состояние обнаружения

Read-only проверка runner: CLI v0.4.0 и checksum прошли; `EDS_API_KEY`,
`EDS_PROJECT_ID`, `CLOUDRU_WORKFLOW_API_KEY` и конфигурация EDS отсутствовали.
Этот факт относится к проверенному runner, не ко всем аккаунтам владельца.
Inventory Repo/Workflow Studio не выполнен; реальные IDs не установлены.
Источник: issue #427, comment 5880128139. Перепроверить перед работой.

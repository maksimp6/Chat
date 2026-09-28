# Alice Pro: EDS + Container Apps + внешний PostgreSQL

Комплект для Codex и оператора, проверен 2026-09-28. Это подготовленные шаблоны
и runbook, **не выполненный деплой**. Канонический репозиторий
`https://github.com/maksimp6/Chat`, ветка `master`. Исходный проверенный master:
`0529307e89c19751321185a238913f50ca151661`; перед работой обновить состояние.

## С чего начать Codex

Прочитать `.codex/skills/cloudru-management/SKILL.md`, затем EDS reference.
Навык лежит в репозитории: он не устанавливает EDS и не создаёт ресурсы сам.

```bash
python scripts/install_eds.py
export PATH="$HOME/.local/bin:$PATH"
eds version
python scripts/cloudru_deploy_preflight.py --help
```

| Файл | Кто использует | Что заполнить |
|---|---|---|
| `eds.config.example.json` | EDS через `EDS_CONFIG` | UUID проекта; API key отдельно в env |
| `control-plane.env.example` | Защищённый deploy runner | EDS и IAM credentials, выбранные IDs |
| `runtime.env.example` | Environment контейнера | DB DSN, app secrets, OAuth, разрешённые GitHub IDs |
| `deployment.example.json` | Наш preflight/runbook | SHA, image digest, project, public origin, DB и egress evidence |
| `container-app.create.example.json` | Evolution Container Apps API adapter | Реальный project/image и секреты только в памяти запроса |
| `postgres/*` | Администратор PostgreSQL/backup runner | Bind IP, DNS/TLS, client CIDRs, роли, backup profile |
| `services.md` | Следующие интеграции | Приоритеты, подтверждённые возможности и текущие задачи |

`deployment.example.json` — **внутренний формат комплекта**, не Terraform,
не OpenAPI и не формат EDS/Workflow Studio. `container-app.create.example.json`
показывает известную структуру POST `/v2/containers` на
`https://containers.api.cloud.ru`; это пример создания, не универсальный PATCH.
В нём нет вымышленных secretRef/volume/logging полей. Перед применением сверить
актуальную публичную API-схему. Для существующего ресурса использовать его ID,
документированный PATCH и сохранение действующей конфигурации.

## Параметры и секреты

Создать рабочие копии в защищённом каталоге вне Git; `deploy/cloudru/private/`
также игнорируется, но не предназначен для сохранения в CI artifacts. Права
секретных файлов 0600, родительского каталога 0700. Не делать `envsubst` в
публикуемый JSON и не передавать значения в командной строке. Env-файлы являются
справочником имён; секреты должен внедрить runner/Secret Management adapter.

| Граница | Обязательные значения |
|---|---|
| EDS | `EDS_PROJECT_ID`, `EDS_API_KEY`, ID существующего приложения для deploy |
| Evolution API | `CLOUDRU_PROJECT_ID`, разрешённая IAM Key ID/Key Secret пара |
| App access | `ALICE_REQUIRE_SHORT_TOKEN=1`, отдельный `ALICE_SHORT_TOKEN` |
| БД | `ALICE_DATABASE_URL` в формате из `postgres/README.md` |
| Encrypted credentials | `ALICE_PROVIDER_CREDENTIAL_KEY`; сохранить прежний при миграции |
| Вход владельца | GitHub client ID/secret, явные allowed IDs, HTTPS callback URI |
| S3, если включён | Bucket/region/tenant + отдельные S3 key ID/secret, минимум прав |
| Foundation Models | Существующий backend provider credential store, отдельный model key |

Для новых app/encryption/DB secrets использовать случайные высокоэнтропийные
значения через защищённый генератор и Secret Management. Не менять существующий
ключ шифрования автоматически: иначе сохранённые provider credentials не прочитать.
Секреты EDS и широкие deploy IAM права в контейнер не попадают. Secret Management
adapter пока отдельная задача #477, KMS — #479: названия будущих env не выдумывать.
Bootstrap identity для чтения Secret Management должна существовать до чтения
секрета; не хранить единственный ключ доступа только за самим этим доступом.

## Порядок подготовки

1. Обновить `master`, прочитать открытые PR #474 (Container Apps API v2) и #476
   (Workflow Studio pipeline). Проверить, что нужные исправления действительно
   merged и протестированы; старый master использует v1, который возвращал 499.
   Не обходить 499 безусловным create — сначала полная инвентаризация.
2. Установить EDS v0.4.0, проверить authorized env/config по наличию, затем сделать
   read-only inventory с пагинацией. Последняя проверка runner не нашла EDS key
   или application ID. IAM-пара не заменяет продуктовый ключ EDS.
3. Подготовить внешний PostgreSQL по `postgres/README.md`: подтверждённый egress,
   firewall/HBA, TLS CA и DNS, роли, backup/restore. Public IP без этих настроек
   не завершает задачу. Не создавать новую VM неявно.
4. Заполнить application env и callback в GitHub OAuth App. Зарегистрированный
   callback должен точно совпадать с публичным origin и `/auth/github/callback`.
   Для изменяемого домена сначала подготовить endpoint без пользовательского
   трафика, затем добавить окончательный callback, затем открыть маршрутизацию.
5. Собрать утверждённый SHA из чистого `git archive`, как в
   `scripts/cloudru_deploy.py`. Записать registry digest и provenance до rollout.
   Использовать порт 8080, UID 10001, 0.5 CPU/1024Mi, min=0/max=1,
   timeout=300s, idleTimeout=600s. Проверить cold start и streaming на выбранном
   протоколе; фоновые задачи в scale-to-zero процессе ненадёжны.
6. Заполнить `deployment.local.json` по example, затем выполнить проверку в
   подготовленной среде (секреты уже в environment):

```bash
python scripts/cloudru_deploy_preflight.py --config deploy/cloudru/deployment.local.json
# Дополнительно проверить существующую EDS цель:
python scripts/cloudru_deploy_preflight.py --config deploy/cloudru/deployment.local.json --workflow
# Из целевого runtime, с CA-файлом и установленным requirements-postgres.txt:
python scripts/cloudru_deploy_preflight.py --config deploy/cloudru/deployment.local.json --check-db
```

Preflight не читает секреты из EDS config, не вызывает cloud API, не проверяет
membership SHA в master, реальную доступность image, подлинность egress evidence
или соответствие всех API payload. Это отдельные проверки оператора/pipeline.
Без `--check-db` он не открывает сетевых соединений. Успех означает `CONFIG_VALID`;
`deployment_ready=false` остаётся явным, потому что rollout checks ещё нужны.
Профиль намеренно фиксирует первоначальный sizing/имена/port и узкие CIDRs;
изменение профиля требует осмысленного изменения validator, а не обхода ошибки.

## Workflow Studio и API rollout

В EDS v0.4.0 create **сразу деплоит**. Сначала установить, как pipeline применит
runtime env, DB CA и доступ к registry до публикации. CLI не умеет настроить эти
поля. Если в Workflow Studio нет подходящего pre-publication hook, использовать
его только после реализации такого pipeline или выбрать существующий API flow.

`eds wf app deploy` не принимает commit SHA. Проверить фактический source SHA и
digest run до promotion; если pipeline не позволяет этого, автоматический
production deploy не готов. Нельзя публиковать произвольный текущий `master`
только потому, что trigger был связан с другим SHA.

Альтернативный API flow в репозитории:
`python scripts/cloudru_deploy.py deploy --tag FULL_MASTER_SHA --env NAME ...`.
Он передаёт env **по именам** и собирает git archive, но новый preflight не
подключён к нему автоматически. До вызова выполнить проверки выше и убедиться
в наличии API v2 исправлений. Существующий workflow требует PostgreSQL и auth,
но его проверка URI scheme не заменяет verify-full/egress/backup проверки здесь.
Не запускать одновременно оба deploy контроллера; выбрать один источник rollout.

JSON-пример env содержит заглушки. Adapter должен заменить значения в памяти
и отправить TLS-запрос с редактированием до trace/log. Сохранение secret-bearing
request/response в `deploy.json`, `tee` или GitHub summary недопустимо.

## Gateway и журналы

Пока нет подтверждённой management API schema Gateway для этого проекта,
не выдавать придуманный Terraform/provider YAML за применяемую конфигурацию.
Настройки для последующего перевода в документированный API/консоль:

| Маршруты | Политика |
|---|---|
| `/healthz` | Только минимальный статус; rate limit, без данных пользователя |
| Login/callback, OAuth discovery/authorization/token | Сохранить соответствующие публичные протокольные шаги, state/PKCE и app auth |
| MCP | Сохранить MCP OAuth и проверку пользователя; blanket cloud API key может сломать клиент |
| Chat/API, персональные ответы | App authorization; tenant/user isolation; кэш выключен |
| SSE/streaming | Кэш и buffering выключены; согласованные timeout и disconnect handling |

API key Gateway идентифицирует клиента, но не заменяет таблицы пользователей,
GitHub owner login, MCP OAuth и историю диалогов. Ограничить размер запроса,
частоту и параллельность по реальному клиенту. Проверить прямой backend URL:
app auth остаётся обязательной, а защита от обхода Gateway quotas требует
поддерживаемого provider-механизма/отдельной интеграции. Не объявлять backend
закрытым только из-за появления Gateway. Начальная API template использует
public ingress и **не реализует** этот дополнительный запрет обхода.

Приложение должно выдавать один JSON object на строку stdout/stderr: timestamp
UTC, level, service, message, request_id, trace_id, duration_ms. User ID — только
если нужен и без прямой PII. Уровни не превращать все в CRITICAL. Не логировать
DSN, токены, query secrets, содержимое prompt/ответов по умолчанию. Это задача
#478; этот комплект не меняет текущий logger и не включает несуществующий флаг.
HTTP request logging включается отдельной настройкой Container Apps; audit logs
фиксируют действия control plane. Все три потока проверять отдельно. Поле
loggingService не добавлено без подтверждённой вложенной API-схемы.

## Проверка результата и rollback

Перед открытием трафика проверить /healthz, негативный anonymous access,
GitHub owner login, чужой GitHub ID, MCP OAuth, чат, чтение/запись PostgreSQL,
сохранность после новой ревизии и холодного старта, streaming, отсутствие
секретов/дубликатов в логах и восстановление backup.

Сохранить **редактированный** отчёт: source SHA, image digest, project/container/
application/revision/run IDs, статус TLS/CRUD/auth/restore, время проверки и
известные ограничения. Не публиковать raw app show/job logs или DSN.

Rollback — возврат проверенного предыдущего digest/revision с совместимой схемой
и теми же secret versions. Остановка run не откатывает уже опубликованный образ;
удаление приложения не rollback. При несовместимой миграции не делать blind
rollback БД; сначала остановить запись и использовать проверенный план restore
в отдельную БД с оценкой потери данных. Не менять существующие данные этим kit.

## Оставшиеся внешние входные данные

Нужны реальный DevServices key/project/application, выбранный PostgreSQL endpoint
и административный канал, гарантированные client CIDRs, CA, runtime secrets,
registry digest, публичный origin и pipeline с подтверждением точного SHA.
Запрос подготовки конфигов не выдаёт эти значения и не подтверждает их наличие.

Источники: [EDS v0.4.0](https://github.com/cloud-ru/evolution-devservices-cli/tree/v0.4.0),
[Container Apps API](https://cloud.ru/docs/container-apps-evolution/ug/topics/api-ref),
[создание контейнера](https://cloud.ru/docs/container-apps-evolution/ug/topics/guides__container-create),
[логи](https://cloud.ru/docs/container-apps-evolution/ug/topics/concepts__logging),
[аудит](https://cloud.ru/docs/container-apps-evolution/ug/topics/monitoring__audit-logging-events).

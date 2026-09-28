# Alice Pro: EDS + Container Apps + внешний PostgreSQL

Комплект для Codex и оператора, проверен 2026-09-28. Это подготовленные шаблоны
и runbook, **не выполненный деплой**. Канонический репозиторий
`https://github.com/maksimp6/Chat`, ветка `master`. Исходный проверенный master:
`0529307e89c19751321185a238913f50ca151661`; перед работой обновить состояние.

## С чего начать Codex

Прочитать `.codex/skills/cloudru-management/SKILL.md`, затем EDS reference и
справочник сервисов Evolution. Навык лежит в репозитории: он не устанавливает
EDS и не создаёт ресурсы сам.

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
| `dns/*`, `api-gateway/*` | DNS/Gateway operator | Шаблон зоны/CNAME и OpenAPI starter; заполнить IDs и все routes |
| `logging/*` | App/platform log operator | JSON stdout event, service-specific logs and redaction checks |
| `identities-and-secrets.md` | Cloud/IAM operator | Пользователи, типы ключей, secret references и сертификаты |
| `.codex/skills/cloudru-management/references/evolution-services.md` | Codex | API boundaries, Workflow, logs, roles, security and AI services |

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

## DNS, API Gateway и журналы

Для пользовательского домена использовать Cloud DNS → API Gateway → Container
Apps: сам Container Apps сейчас выдаёт provider URL без пользовательского host.
`dns/README.md` описывает inventory и CNAME, `dns/*.example.json` — тела
документированных DNS операций, а `api-gateway/alice-openapi.example.json` —
частичный OpenAPI 3.0 starter с Container Apps backend, rate-limit примером и
log-group reference. Это не полный route map и не выполненный gateway deploy:
до импорта заполнить реальные UUID и описать все используемые маршруты. Подключить
custom domain к сертификату Certificate Manager; сверить TLS, OAuth callback,
MCP metadata и поведение прямого Container Apps URL. Не менять production DNS
до end-to-end проверки и плана отката.

Gateway default address не аутентифицирует пользователя. Политики API-key, IAM
или поддерживаемая OIDC policy защищают gateway/client boundary, а Alice сохраняет
GitHub owner login, short token, MCP OAuth, user ownership и authorization. Не
включать общий cache для персональных/API/MCP/streaming routes. Gateway rate
limit — агрегат route/window в примере, не per-user quota; проверять backend
direct-access и streaming timeout до production.

Конкретные потоки и JSON event описаны в `logging/README.md` и
`.codex/skills/cloudru-management/references/evolution-services.md`: JSON
stdout/stderr, Container Apps system/request logs, Gateway log group, Cloud audit
и AI service telemetry — разные потоки. Request logging для chat/MCP включать
только после проверки фактически записываемых headers, query, body и redaction;
не писать токены, prompts, DSN, cookies или private keys. Конфигурацию приложения
для JSON logger ведёт задача #478; этот комплект не выдумывает неизвестные поля
Container Apps API.

`identities-and-secrets.md` перечисляет IAM users/service accounts, EDS key,
Workflow Studio API, Gateway users/keys, FM/S3 keys, DB roles и certificate
material. Runtime secret injection остаётся gated adapter/pipeline work (#477);
placeholder `REPLACE_FROM_SECRET_STORE` пока не native secret reference.

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

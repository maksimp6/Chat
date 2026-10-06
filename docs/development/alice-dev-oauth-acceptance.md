# Alice Dev: публичный OAuth origin и проверка подключения

Каноническая задача: #939; реализация первого исправления: #941.

## Конфигурация

`ALICE_DEV_PUBLIC_URL` задаётся deployment-кодом, не пользователем. Это не секрет.
Для commit-worker желаемый origin: `https://alice-dev-<project12>-<sha8>.containerapps.ru`.
До принятия контейнера код сверяет этот адрес с `publicUri` из Cloud.ru и проверяет
provider UUID. Несовпадение останавливает rollout. `Host` и `X-Forwarded-*` не
используются для выбора OAuth issuer или resource. Неизвестная/неверная конфигурация
отклоняется до запуска HTTP listener. HTTP разрешён только для локальных тестов.

Существующий short token не меняется. Новых ручных service tokens этот срез не вводит.

## Проверки

`oauth-discovery-smoke.mjs` проверяет health JSON, 401 с resource metadata,
protected-resource metadata и authorization-server metadata. Общий лимит: 60 секунд,
один HTTP-запрос вместе с чтением тела: максимум 5 секунд. Повторяются только сетевые
сбои/тайм-ауты и ответы 502/503/504. Неверный 200/403/404, отсутствующее metadata или
несовпадение issuer остаются ошибкой, а не основанием пропустить проверку.

Лог содержит только stage, attempt, status и безопасный код ошибки. Внешний URL
из неожиданного challenge не запрашивается. Проверка не отправляет credentials.
После неё отдельно выполняется authenticated MCP initialize/list/get_config.
Manifest считается принятым только после обеих проверок.

Regression tests запускаются через `node --test` в image-validation workflow.
Они поднимают настоящий HTTP gateway со stub upstream, проверяют proxy headers,
отказ от неверного origin и ограничение ожидания зависшего HTTP-сервера/тела ответа.
Python tests проверяют deployment env, совпадение адреса и валидность provider ID.

## Что доказано, а что ещё нет

Лог run `37503182752`, job `112405107587`, содержит `502 !== 401` на первой проверке
MCP. Это не доказательство получения 401 без resource_metadata. Точные proxy headers
этот лог не сохраняет. Первоначальное объяснение через пустые headers было гипотезой.

Локальные tests не заменяют live acceptance. Успешные metadata + bearer MCP smoke
также не доказывают вход из ChatGPT, browser consent или сохранение OAuth-сессии
после scale-to-zero/redeploy. OAuth state пока хранится локально в контейнере;
проверка восстановления состояния и полноценного OAuth-подключения остаётся открытой.
Реальные credentials нельзя передавать до принятия соответствующего канала.


## Durable OAuth runtime contract (#939)

The Alice Dev gateway can persist its complete OAuth file as closed snapshots in
an explicitly provisioned private Object Storage mount. It does not use the Chrome
profile store, SQL, a remote browser profile, or last-writer-wins recovery.

Runtime configuration:

| Variable | Contract |
| --- | --- |
| `ALICE_DEV_OAUTH_DURABLE_DIR` | Exact mount root dedicated to this OAuth origin/owner. Runtime checks `/proc/self/mountinfo`; an ordinary directory is not an accepted production mount. |
| `ALICE_DEV_OAUTH_STATE_FILE` | Disposable local POSIX cache; default `/tmp/alice-dev-auth/oauth.json`. Must be outside the mount. |
| `ALICE_DEV_OAUTH_REQUIRE_DURABILITY=1` | Refuse startup when no durable store is configured. |
| `ALICE_DEV_PUBLIC_URL` | Canonical origin, equal to the mount's owner marker. |
| `ALICE_DEV_OWNER_ID` | Owner marker binding; defaults to `owner`. |

Provisioning must explicitly create `owner.json` with exactly `schema: 1`,
`purpose: "alice-dev-oauth"`, `origin` and `owner_id`. The application never creates
or adopts an unowned mount. Existing local-only registrations require a deliberate,
quiesced migration; they are not silently imported. Changing the origin constitutes
a different OAuth issuer, not a transparent registration migration.

Restore completes before the signing key is loaded, the upstream starts, or HTTP
readiness is exposed. Each state-changing OAuth request is serialized. DCR, consent
redirects, owner-session cookies, code exchange, refresh and revocation wait for a
verified durable write. MCP token checks use the last acknowledged state, not a
mutation that is still awaiting persistence. A failed checkpoint poisons that
runtime: OAuth and health return 503 until a new runtime recovers verified state.
Errors contain fixed codes, not tokens, cookies, filesystem paths or snapshot data.

The mount contains `owner.json`, `intent.json`, `commit.json` and two bounded
`state-N.json` slots. Every write is closed and read back, with size/SHA-256 checks.
Only the disposable local cache uses rename. A mismatched intent/commit, corrupt
latest snapshot or observed stale writer fails closed. Recovery never selects an
older token snapshot, even if its checksum is valid: doing so could undo revocation
or refresh-token replay detection.

**Single-writer requirement:** mounted Object Storage does not provide a distributed
compare-and-swap/lock. Generation checks reject an already observed newer writer,
but are not proof against two truly simultaneous writers. Deployment/lifecycle must
prove exclusive ownership before enabling this backend. Overlapping replicas are a
release blocker, not permission to adopt newer state or retry a failed write.
A mount operation is bounded to 10 seconds; after timeout the runtime is poisoned,
since an in-flight filesystem operation cannot be cancelled reliably.

### Evidence and activation boundary

The existing `oauth.test.mjs` CI entrypoint imports the snapshot and fresh-process
regressions. The integration test creates seven separate gateway processes, kills
each with SIGKILL and uses a new local cache directory on each start. The same DCR
registration, pending consent/cookie, authorization code, access/refresh tokens and
replay-triggered revocation must survive. It uses real HTTP and the MCP SDK with a
synthetic upstream. Its durable directory is an explicitly labelled local fixture,
not evidence about Cloud.ru mount semantics or platform lifecycle.

This runtime slice does **not** provision a bucket, attach a volume, change the
current Cloud.ru candidate spec, deploy an image or migrate existing credentials.
The current candidate remains local-only until that separate owner-approved
activation is completed. Required live acceptance remains: lifecycle evidence,
exclusive writer, real mount, same client after idle >10 seconds and a confirmed
restart, OAuth-token MCP/refresh and browser onboarding for each actual client.
A local PASS or green image validation must not be reported as fixed ChatGPT login.

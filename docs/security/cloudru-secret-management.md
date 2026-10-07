# Cloud.ru Secret Management для секретов Alice Pro

Статус: Cloud.ru adapter, pinned version refs/rollback и redaction foundation shipped. Исторические #477/#440 дали первый backend-specific этап; текущая canonical ownership — #755. Provider-neutral `SecretRef` / `SecretValue` / `SecretResolver` contract уже существует в `secret_store/core.py`, а consumer-by-consumer migration остаётся незавершённой.

Источники (проверено 2026-09-29 МСК):

- https://cloud.ru/docs/scsm/ug/doc-contents
- https://cloud.ru/docs/scsm/ug/topics/overview__limitations
- https://cloud.ru/docs/scsm/ug/topics/pricing

В репозитории пока нет зеркала этой документации
(`docs/integrations/cloudru-docs-mirror.md` в статусе "planned"), поэтому
точные пути REST API и коды состояний в `cloud/cloudru/secret_management.py`
— наилучшее известное соответствие документированной модели (секрет → много
неизменяемых версий), а не подтверждённый на живом проекте контракт. Тот же
явный disclaimer уже есть для Container Apps в
`docs/cloudru-container-apps.md`. Проверить и при необходимости поправить
пути/коды при первом реальном подключении.

## Canonical и compatibility layers

Canonical application contract — `secret_store/core.py`: typed `SecretRef(provider, secret_id, version_id, purpose)`, opaque non-serializable `SecretValue` и `SecretResolver`. Durable state должен хранить только reference/version metadata.

`secret_management_refs` и helpers в `provider_credentials.py` — shipped Cloud.ru compatibility implementation, существовавшая до provider-neutral contract. Она остаётся рабочей и протестированной, но новые consumers должны ориентироваться на canonical resolver; legacy SQL metadata/value paths удаляются только после проверенного cutover по #755.

## Cloud.ru version model

Секрет в Cloud.ru Secret Management состоит из версий; версия после записи
неизменяема, и несколько версий могут быть одновременно активны. Alice Pro не
меняет эту модель. Вместо этого приложение хранит локально только **ссылку**:
какую версию оно сейчас считает действующей для каждого назначения
(`purpose`).

Таблица `secret_management_refs` (`provider_credentials.py`):

| Колонка               | Смысл                                                    |
| ---------------------- | --------------------------------------------------------- |
| `purpose`               | Ключ назначения, например `alice_short_token`             |
| `secret_id`             | Идентификатор секрета в Cloud.ru                           |
| `pinned_version_id`     | Версия, которую сейчас читает backend                      |
| `previous_version_id`   | Предыдущая закреплённая версия — для явного отката          |
| `updated_at`            | Когда ссылка менялась в последний раз                       |

Само значение секрета в этой таблице никогда не хранится.

```python
from provider_credentials import (
    set_secret_management_ref,
    rollback_secret_management_ref,
    resolve_secret_management_value,
)

# Закрепить/переключить версию — старая версия не изменяется в Cloud.ru,
# только меняется локальная ссылка.
set_secret_management_ref(db, "alice_short_token", secret_id="...", version_id="v2")

# Явный откат к прежней закреплённой версии.
rollback_secret_management_ref(db, "alice_short_token")

# Получить значение — только внутри доверенного backend-кода.
# Результат нельзя логировать, трейсить, отдавать в MCP/UI/ошибки.
token_value = resolve_secret_management_value(db, "alice_short_token")
```

`resolve_secret_management_value` не имеет отката на локальное
незашифрованное хранилище: если Cloud.ru Secret Management недоступен, вызов
падает с `CloudProviderError`, а не читает/пишет secret в открытом виде.

## Два разных уровня прав

Ключевое требование issue #477: обычные сессии/модели никогда не должны
получить административный IAM-ключ.

- **Runtime (только чтение).** `CloudRuSecretManagementClient`
  (`cloud/cloudru/secret_management.py`) использует пару
  `CLOUDRU_SECRET_MANAGEMENT_KEY_ID` / `CLOUDRU_SECRET_MANAGEMENT_KEY_SECRET`.
  В Cloud.ru IAM этой служебной учётной записи следует выдать только роль
  просмотра (viewer) Secret Management — доступ на чтение payload закреплённых
  версий, без права создавать/отключать версии или менять политики доступа.
- **Администрирование.** Создание секретов, новых версий, отключение старых
  версий и управление правами доступа выполняется существующей
  административной парой `CLOUDRU_IAM_KEY_ID` / `CLOUDRU_IAM_KEY_SECRET`
  (`cloudru_iam.py`), которая уже используется для ротации provider-ключей
  (`provider_key_rotation.py`) и деплоя (`container_apps_client.py`). Эта пара
  не участвует в чтении секретов на runtime-пути и не должна попадать в
  конфигурацию обычной сессии или модели.

Bootstrap-идентичность (`CLOUDRU_SECRET_MANAGEMENT_KEY_ID/SECRET`) задаётся
обычными переменными окружения процесса, а не значением, которое само
хранится в Secret Management — иначе получилась бы циклическая зависимость
(нельзя прочитать секрет, чтобы узнать, чем читать секреты).

## Где значение секрета может появляться, а где — никогда

Разрешено (backend-only путь):

- `CloudRuSecretManagementClient.get_secret_value(...)` — единственная точка,
  которая возвращает plaintext, и только напрямую вызывающему коду;
- `resolve_secret_management_value(db, purpose)` — тонкая обёртка поверх неё
  для существующей границы `provider_credentials.py`.

Запрещено и проверено тестами (`tests/test_cloudru_secret_management.py`,
`tests/test_provider_credentials_secret_management.py`):

- обычный трейсируемый HTTP-клиент (`CloudRuClient.request`, тот же путь, что
  используют остальные Cloud.ru API) — `get_secret_value` намеренно его не
  использует, поэтому тело ответа secret API физически не проходит через код,
  который сериализует HTTP-трафик в Execution Trace;
- события `provider_api_request`/`provider_api_response` для операции
  `get_secret_value` содержат только `secret_id`, HTTP-статус и время — не
  тело ответа;
- сообщения исключений (`CloudProviderError`) — только фиксированный текст,
  код ошибки и HTTP-статус, никогда не тело ответа сервера;
- MCP/UI-инструменты: `CloudRuSecretManagementClient` намеренно не реализует
  протокол `cloud.base.CloudProvider` и не регистрируется в
  `cloud/registry.py`, поэтому он структурно недостижим из generic
  cloud-инструментов (`cloud/tools.py`), которые вызывает модель;
  административный клиент (`cloudru_iam.py`) для чтения payload значений не
  используется вовсе.
- вложенные ошибки: если код, вызвавший `resolve_secret_management_value`,
  падает позже с уже полученным значением в области видимости и попадает в
  `ExecutionTrace.record_error(..., exception=...)`, значение секрета не
  должно оказаться в снимке трейса. Общий механизм редактирования локальных
  переменных в `trace_manager.py` уже редактирует по имени переменной любое
  имя, содержащее `secret`/`token`/`password`/`api_key` — поэтому весь код,
  работающий со значением секрета в этом модуле и в
  `provider_credentials.py`, называет соответствующую локальную переменную
  `secret_value`/`secret_plaintext`, а не общим именем вроде `value`.

## Предсказуемые ошибки, таймауты и работа с plaintext

- Таймаут запроса и число повторов ограничены (`timeout`, `max_retries`);
  повтор выполняется только при сетевой ошибке, а не при явном HTTP-ответе
  403/404/409 — такие ответы не путаются с временной недоступностью.
- 401 → `auth_failed`, 403 → `authorization_failed`, 404 → `not_found`, 409 →
  `version_disabled` (наилучшее известное соответствие, см. предупреждение
  выше). Код ошибки стабилен и пригоден для программной обработки вызывающим
  кодом.
- Secret Management client **не держит process-global plaintext cache**.
  Каждый вызов `get_secret_value` получает payload заново и передаёт его
  прямому backend-caller. Параметры `cache_ttl`/`cache_max_entries`
  сохранены лишь для совместимости API и не разрешают client-side cache.
- Пока request-scoped `ExecutionTrace` остаётся mutable, plaintext временно
  хранится только во внутреннем redaction registry, чтобы очищать последующие
  trace events по значению. После успешного `finalize()` registry очищается,
  а frozen snapshot уже содержит только redacted данные.
- Нет отдельного "разрешённого" fallback-пути на чтение/запись в открытом
  виде — при недоступности API вызывающий код получает исключение.

## Подключение секретов Alice Pro (без самих значений)

Ниже — процедура подключения по каждому назначению. Реальные значения
секретов никогда не должны попадать в issue, PR, лог или артефакт CI.

1. В консоли/CLI Cloud.ru Secret Management создать секрет и записать первую
   версию значения (вне репозитория, вручную владельцем).
2. Убедиться, что runtime-идентичность (`CLOUDRU_SECRET_MANAGEMENT_KEY_ID` /
   `_SECRET`) имеет только роль просмотра на этот секрет.
3. Закрепить ссылку в Alice Pro:
   `set_secret_management_ref(db, purpose, secret_id, version_id)`.
4. Там, где сегодня код читает значение из переменной окружения или из
   `provider_credentials`/`key_manager`, для соответствующего `purpose`
   вызвать `resolve_secret_management_value(db, purpose)` вместо чтения
   локального plaintext. Эта замена — отдельный, следующий узкий PR по
   каждому назначению, не часть данного этапа.

Назначения (`purpose`), которые canonical #755 мигрирует на эту схему:

- `alice_short_token` — короткий токен доступа (`short_token_auth.py`,
  `ALICE_SHORT_TOKEN`);
- `github_oauth_client_id` / `github_oauth_client_secret` — GitHub OAuth
  (`identity/github_oauth.py`);
- `provider_credential:yandex` / `provider_credential:cloudru` — ключи провайдеров LLM, которые пока имеют legacy encrypted-value storage в `provider_credentials.py`; после verified resolver cutover #755 требует остановить legacy writes и удалить этот secret-value path, сохранив только допустимые non-secret/reference metadata;
- `alice_database_url` — строка подключения к управляемому PostgreSQL
  (`ALICE_DATABASE_URL`), см. известное ограничение "App secrets are plain
  container env vars" в `docs/cloudru-container-apps.md`.

## Реальный bootstrap из Container Apps

На сегодня (см. `docs/cloudru-container-apps.md`, раздел "Known
limitations") секреты Container Apps — обычные переменные окружения
контейнера, видимые всем, у кого есть доступ на чтение к Container Apps в
проекте. Целевая последовательность для перехода на Secret Management:

1. Container Apps передаёт в контейнер только bootstrap-идентичность:
   `CLOUDRU_SECRET_MANAGEMENT_KEY_ID` и `CLOUDRU_SECRET_MANAGEMENT_KEY_SECRET`
   (viewer-роль), плюс `CLOUDRU_PROJECT_ID`. Эти значения не секрет более
   высокого уровня, чем сами по себе — они дают право прочитать только то,
   что явно открыто для чтения политикой Secret Management, и не дают права
   администрирования.
2. При старте backend читает ссылки (`secret_management_refs`) и вызывает
   `resolve_secret_management_value` по каждому нужному `purpose`, вместо
   того чтобы ожидать готовое значение в переменной окружения.
3. Административная пара (`CLOUDRU_IAM_KEY_ID`/`CLOUDRU_IAM_KEY_SECRET`)
   остаётся вне рантайма приложения — она нужна только тем, кто создаёт
   секреты/версии и настраивает права (сегодня это уже так для
   `scripts/cloudru_deploy.py` и ротации provider-ключей).
4. Реальные значения (`ALICE_SHORT_TOKEN`, OAuth-секреты, provider-ключи,
   `ALICE_DATABASE_URL`) перестают быть переменными окружения контейнера и
   становятся версиями в Secret Management; Container Apps перестаёт быть
   местом, где они видны в открытом виде.

Bootstrap/cutover не считается завершённым только из-за наличия adapter/reference code. Для каждого consumer #755 требует явный target reference/version, успешное resolution evidence и проверку missing/revoked/unavailable; `SKIPPED` не является доказательством migration.

## Связанные материалы

- `docs/security/key_manager.md` — общий менеджер ключей Alice Pro
  (внутреннее шифрование, независимо от Cloud.ru).
- `docs/cloudru-container-apps.md` — деплой и известные ограничения по
  секретам.
- Historical implementation issues: #477, #475, #440.
- Canonical current ownership: #755.

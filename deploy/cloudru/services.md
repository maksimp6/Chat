# Evolution services для Алисы

Сверено с официальной документацией 2026-09-28. Это целевая архитектура и
операторские конфиги, не созданные облачные ресурсы. Preview/GA, доступность,
квоты и тариф перепроверять в конкретном проекте перед provisioning.

| Сервис | Как использовать | Готовность / граница |
|---|---|---|
| Container Apps | Alice HTTP runtime | Пример и preflight в этом каталоге; custom domain не поддерживается самим Container Apps |
| Внешний PostgreSQL | Пользователи, история, настройки | TLS verify-full, проверенный egress/firewall, отдельная app role и проверенный restore обязательны |
| Cloud DNS | Зона и CNAME пользовательского поддомена на API Gateway | Примеры запросов в `dns/`; до делегации сохранить и проверить все существующие записи |
| API Gateway | Публичный domain, маршрутизация к Container Apps, API-client policy, rate limits и request logs | OpenAPI starter в `api-gateway/`; он покрывает только health/chat/MCP и требует реальных IDs, полного route map и end-to-end тестов |
| Workflow Studio / EDS | Сборка и публикация утверждённого source | EDS CLI v0.4.0 проверен; EDS product key отсутствует в runner. `eds wf app create` сразу запускает deploy; branch не закрепляет SHA |
| IAM users / service accounts | Люди в консоли и service identity для control-plane/API | Присваивать org/project/service roles по минимуму; IAM Key ID+Secret обменивается на короткоживущий IAM Bearer token |
| Gateway API keys | Auth для выделенных machine-to-machine callers | Не класть общий ключ в Alice frontend; Gateway IAM/API-key policy не заменяет Alice GitHub login, owner check или MCP OAuth |
| Secret Management | Хранить версии deploy/runtime DB, OAuth, model и S3 секретов | Adapter/pipeline read path #477 не реализован; `REPLACE_FROM_SECRET_STORE` в примере не является native reference |
| Certificate Manager | TLS certificate для Gateway custom hostname | Хранить приватный ключ в сервисе; проверить domain, выдачу/renewal и binding. DB CA — отдельная задача |
| Key Management | Ключевые операции для зашифрованных PostgreSQL backups в Object Storage | #479/#447; verify decrypt/restore с той же версией ключа. Не включать plaintext fallback |
| Container Apps logs / Cloud Logging | Runtime stdout/stderr, platform/request logs | JSON stdout идёт отдельной задачей #478; request logs включать после проверки чувствительных полей |
| Cloud audit logs | Кто менял control-plane ресурсы и когда | Это отдельный поток от приложения, HTTP request и EDS job logs |
| Foundation Models + Guardrails | Возможная отдельная будущая оценка AI сервисов Evolution | Не являются текущим AI backend Алисы: inference сейчас идёт только через Yandex Cloud / Yandex AI Studio; не включать в текущий runtime |
| AI Agents / EvoClaw / AI Workflows | Изолированный пилот агентов, MCP servers и AI workflows | Самостоятельные сервисы Evolution; не переключать Alice runtime и не обходить `UniversalToolExecutor`/approvals |
| Agents Space | Песочница на базе EvoClaw | Оплата pay-as-you-go: текущая документация указывает baseline 2 vCPU/4 GB за 3.84 ₽/час на работающего агента, плюс model/storage/egress |
| Distributed Train | Трекинг ML экспериментов и отдельно — обучение | Бесплатный tracking preview не означает бесплатные GPU compute/jobs |
| Artifact Registry | OCI images по digest и private NPM/PyPI packages | Хранение, egress, lifecycle и статус каждого формата/кэширования считать отдельно |
| Object Storage | Пользовательские объекты и зашифрованные backup | Private bucket, отдельный S3 principal/keys, retention/lifecycle; не PostgreSQL PGDATA |
| Managed OpenSearch | Поздний поиск/аналитика | Отдельная платная compute/storage статья, retention и исключение секретов из индекса |

## Порядок внедрения

1. Завершить Container Apps API/pipeline и runtime secret adapter; зафиксировать
   image digest, source SHA, переменные и CA до публикации.
2. Проверить external PostgreSQL connection с verify-full, узкой сетевой политикой,
   non-superuser ролью и restore drill.
3. Настроить least-privilege IAM/service identities; вести инвентарь ID, scope,
   назначения и rotation даты без самих secret values. Типы ключей перечислены в
   [`identities-and-secrets.md`](identities-and-secrets.md).
4. Создать/проверить Certificate Manager сертификат и Gateway в test domain,
   затем полный OpenAPI route map. Не включать пользовательский трафик до
   проверки login, MCP OAuth, upload/voice, CORS, streaming, limits и direct URL.
5. Перенести домен через Cloud DNS только после инвентаризации записей и DNS/TLS
   проверки; подготовить rollback snapshot до изменения production CNAME/NS.
6. Включить и проверить app JSON, Container Apps HTTP, Gateway, audit и EDS job
   logs раздельно; тестовыми маркерами доказать отсутствие секретов/body leakage.
7. Подключать Guardrails/AI Agents отдельным canary: тестировать вход/выход,
   отказ/таймаут, tool approvals, cross-user isolation, telemetry redaction и
   цену зависимых model/storage/compute ресурсов.
8. Подключить KMS к зашифрованной копии DB в private Object Storage, затем
   восстановить в отдельную базу до плановой ротации ключа.

Не обещать «всё бесплатно»: preview service может быть без SLA/лимитированным,
но его зависимости, inference, running agents, storage, traffic и registry
могут тарифицироваться. Проверять цены и стадии в официальном каталоге до
создания ресурсов.

## Первичные источники

- [Стадии запуска Evolution](https://cloud.ru/docs/evolution/overview/topics/services__launch-stages)
- [Cloud DNS API: зоны](https://cloud.ru/docs/clouddns/ug/topics/api-ref_zone)
- [Cloud DNS API: записи](https://cloud.ru/docs/clouddns/ug/topics/api-ref_resource-record)
- [API Gateway extensions](https://cloud.ru/docs/api-gateway-svp/ug/topics/concepts__apigw-extensions)
- [API Gateway policies](https://cloud.ru/docs/api-gateway-svp/ug/topics/guides__configure-policy)
- [API Gateway release notes](https://cloud.ru/docs/api-gateway-svp/ug/topics/overview__release-notes)
- [Workflow Studio API authentication](https://cloud.ru/docs/pipeline/ug/topics/api-ref__authentication)
- [Secret Management access](https://cloud.ru/docs/scsm/ug/topics/access)
- [Certificate Manager access](https://cloud.ru/docs/certificate-manager/ug/topics/access)
- [Foundation Models Guardrails](https://cloud.ru/docs/foundation-models/ug/topics/concepts__guardrails)
- [AI Agents roles](https://cloud.ru/docs/ai-agents/ug/topics/access)
- [Agents Space pricing](https://cloud.ru/docs/agents-space/ug/topics/pricing)
- [Artifact Registry release notes](https://cloud.ru/docs/artifact-registry-evolution/ug/topics/overview__release-notes)
- [Managed OpenSearch pricing](https://cloud.ru/docs/opensearch/ug/topics/pricing)

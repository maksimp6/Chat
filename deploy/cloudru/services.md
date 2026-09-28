# Сервисы Cloud.ru для Алисы: порядок подключения

Проверка 2026-09-28. Запрос владельца — использовать перечисленные сервисы,
особенно безопасность. Это план интеграций; наличие строки не означает, что
ресурс создан или adapter уже реализован. Текущие цены/стадию/доступность
перепроверять для выбранного проекта перед запуском.

| Сервис | Роль для Алисы | Статус внедрения / условия |
|---|---|---|
| EDS Repo / Workflow Studio | Источник/сборка/публикация | CLI v0.4.0 проверен; product key в runner не найден; pipeline #476 |
| Container Apps | Runtime | Начальный 0–1 экземпляр; API v2 #474; стоимость compute/трафика отдельно |
| Внешний PostgreSQL | Пользователи, история, настройки | Выбор владельца; TLS/egress/backup в этом комплекте; стоимость сервера отдельно |
| API Gateway | Routing, throttling, API client auth | Preview; проверить тариф/стадию; не заменяет app OAuth и DB; management schema нужна |
| Secret Management | Версии DB/model/OAuth/S3 secrets | Бесплатен по текущей pricing page; adapter #477, сначала безопасность bootstrap/read/redaction |
| Key Management | Ключ для защиты backup в S3, дальнейшая ротация | Бесплатен по pricing page; #479 и #447; права encrypt/decrypt отдельно от admin |
| Foundation Models + Guardrails | Модели и маскирование чувствительных данных | Стоимость model inference отдельно; правила входа/выхода тестировать на canary |
| Object Storage | Файлы и зашифрованные backup | Закрытый bucket, lifecycle/versioning, отдельные S3 credentials; не live PGDATA |
| Artifact Registry OCI | Образы по digest | Требуется для runtime; проверить стоимость хранения/трафика и lifecycle |
| Artifact Registry NPM | Приватные JS-пакеты | NPM Preview заявлен бесплатным; не нужен как блокер первого deploy |
| Artifact Registry PyPI | Приватные Python-пакеты | GA и платный с июня 2026; caching GA/платный с сентября 2026 |
| Managed OpenSearch | Позднее поиск по документам/логам | **Платный** CPU/RAM/storage; отдельный лимит расходов, retention, индекс без секретов |
| Agents Space / AI Agents | Отдельные эксперименты с агентами | Не замена runtime Алисы; доступ/ключи/квоты/тариф уточнить, сохранить ToolExecutor boundary |
| Distributed Train tracking | ML experiments через совместимый SDK | Бесплатный Preview tracking не делает GPU training бесплатным; отдельные credentials/project |
| Logging / audit | Диагностика и трассировка | #478: JSON stdout; HTTP и audit настраиваются отдельно от logger |

## Приоритеты и критерии

1. **Secret Management (#477).** Выделить runtime service identity с минимальным
   доступом к нужным secrets/versions. Зафиксировать version IDs для deploy и
   rollback; не полагаться на изменяемый latest. Negative tests: нет доступа,
   timeout, malformed value, redaction в exception/trace. Rotation с overlap,
   без удаления старой версии до успешного перехода. Existing deployment key
   шифрования сохранить. Не придумывать переменные/REST payload до adapter.
2. **JSON logs (#478).** Один JSON event/строка с request/trace correlation;
   отсутствие plaintext дублей и общего CRITICAL override; единая очистка DSN,
   token-bearing URLs, HTTP headers, PEM и payload до stdout/ExecutionTrace.
   Проверить в контейнере UID 10001 без обязательной записи local log files.
3. **KMS (#479) + backup (#447).** Первый измеримый сценарий — шифрование backup,
   загрузка в закрытый S3 и восстановление с тем же ключом/версией. Сервисные
   права `sckm.user` для операций с данными, административные отдельно.
   Проверить wrong key/version, denied decrypt, повреждённый ciphertext и audit;
   не делать fallback на plaintext. KMS не шифрует автоматически БД по TCP.
4. **Gateway + Guardrails.** Сохранить owner/MCP authorization. Редактировать
   чувствительные данные до LLM/trace. Guardrails не является доказательством
   отсутствия галлюцинаций или prompt injection; демаскировка не должна раскрыть
   данные другому пользователю. Проверить streaming/tools/cross-user isolation.
5. **Registry packages, OpenSearch, Agents, experiments (#475).** Подключать
   отдельными вертикальными PR с тестовым сценарием, service identity, квотами,
   бюджетом и удалением тестовых артефактов. Не выдавать большой кластер/обучение
   за бесплатный Preview пилот. Наличие GPU endpoint — не причина запускать job.

Общие Preview-условия: бесплатно, если специальные условия не говорят иначе;
нет SLA, ограничения ресурсов и риск приостановки/удаления при переходе в GA.
Не все продукты в пользовательском списке находятся в Preview. Экономию считать
по каждому сервису и зависимостям, без обещания «всё бесплатно».

## Первичные источники

- [Статусы Preview/GA](https://cloud.ru/docs/evolution/overview/topics/services__launch-stages)
- [API Gateway](https://cloud.ru/docs/api-gateway-svp/ug/topics/overview__release-notes)
- [Secret Management: стоимость](https://cloud.ru/docs/scsm/ug/topics/pricing)
- [Secret Management: версии/ограничения](https://cloud.ru/docs/scsm/ug/topics/overview__limitations)
- [KMS: стоимость](https://cloud.ru/docs/kms/ug/topics/pricing)
- [KMS: доступ](https://cloud.ru/docs/kms/ug/topics/overview__limitations)
- [S3 SSE-KMS](https://cloud.ru/docs/s3e/ug/topics/concepts__encryption-sse-kms)
- [Guardrails](https://cloud.ru/docs/foundation-models/ug/topics/concepts__guardrails)
- [Artifact Registry release notes](https://cloud.ru/docs/artifact-registry-evolution/ug/topics/overview__release-notes)
- [OpenSearch: стоимость](https://cloud.ru/docs/opensearch/ug/topics/pricing)
- [Август 2026: Agents Space и tracking](https://cloud.ru/blog/daydzhest-avgust-2026)

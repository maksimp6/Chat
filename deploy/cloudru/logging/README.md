# Логи Alice и Evolution

`app-event.example.json` — пример **одной stdout JSON-строки** приложения.
Это схема для будущего logger (#478), а не Container Apps resource payload и не
включатель облачного логирования. В runtime сериализовать ровно один объект на
строку, писать в stdout/stderr и flush; не сохранять обязательные файлы в
container filesystem. Заполнять route template, а не URL с query-параметрами.

До сериализации очищать exception, message, URL, headers, tool args и trace от
Authorization/cookies, IAM/EDS/API/S3/FM keys, DSN/passwords, private keys,
prompt/response и upload contents. Не включать request/response body в общий
access log. Тестировать redaction строками canary, которые никогда не похожи на
реальные production secrets. `request_id`/`trace_id` — корреляция, не авторизация;
не использовать email или Cloud credentials как log labels.

| Поток | Настройка/пример | Область |
|---|---|---|
| Alice application | stdout/stderr JSON, event fields в `app-event.example.json` | Логика приложения; текущий Python logger отдельная работа #478 |
| Container Apps platform | Console/runtime logs и отдельная опция request logging | Запуск/scale и HTTP request metadata; перед включением проверить, какие поля собираются |
| API Gateway | `x-cloud-log-group: <UUID>` на path/operation OpenAPI | Gateway request logs; UUID создаётся в сервисе Logging, сам extension не задаёт redaction |
| Cloud audit | Audit logs | Actor и изменения control plane, не содержание Alice request |
| Workflow Studio / EDS | Job/build output | Pipeline и deploy; вывод может содержать build env и должен быть закрыт/редактирован |
| AI services | Logaas, Monas, Audaas по соответствующему сервису | Agent logs, metrics и audit; отдельные IAM roles, retention и redaction |

Не подключать log group к `/api/chat`, MCP, OAuth или upload routes, пока не
проверены фактические поля, headers, query и body на test gateway. Синтетический
canary secret не должен появиться ни в одном потоке. Ограничить readers и срок
хранения, затем отдельно проверить alerting и удаление/retention. Gateway request
logging, Container Apps request logging, app stdout и audit logs — независимые
переключатели. Отключение одного не отключает остальные.

Пример `x-cloud-log-group` в
[`../api-gateway/alice-openapi.example.json`](../api-gateway/alice-openapi.example.json)
содержит placeholder, который надо заменить существующим Logging group UUID.
Прежде чем включать request logging для Alice private routes, убедиться, что
сервис не сохраняет секретные поля; выбирать минимальный набор маршрутов.

Источники: [Container Apps logs](https://cloud.ru/docs/container-apps-evolution/ug/topics/concepts__logging),
[API Gateway x-cloud-log-group](https://cloud.ru/docs/api-gateway-svp/ug/topics/concepts__apigw-extensions),
[API Gateway logs](https://cloud.ru/docs/api-gateway-svp/ug/topics/overview__release-notes),
[Container Apps request logging](https://cloud.ru/docs/container-apps-evolution/ug/topics/guides__container-create).

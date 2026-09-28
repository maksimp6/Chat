# API Gateway для Alice Pro

`alice-openapi.example.json` — намеренно маленький starter для
Shared API Gateway OpenAPI template: `/healthz`, `/api/chat`, `/mcp`. Он **не
описывает полный Flask route map Алисы** и не готов для импорта/публикации, пока
не заполнены UUID Container Apps, log group и limits, не добавлены остальные
routes UI/API и не выполнены проверки ниже. Изменение файла само ничего в облаке
не создаёт. OpenAPI JSON принимается Cloud Gateway; расширения документированы.

Backend использует документированный `serverless_container` и `container_id`.
Проверь в проекте, что UUID относится к Alice Container Service, gateway имеет
доступ к выбранной ревизии и запрос достигает containerPort 8080. Пробный backend
сначала подключать к тестовой ревизии. Если провайдер требует другой объект ID
или поддерживает маршрут через public URL, сверить с актуальной схемой Cloud
Gateway до изменения конфигурации. Не подставлять container name вместо UUID.

## Заполнить gateway

1. Выбрать Shared → OpenAPI, загрузить валидный JSON, разобрать все предупреждения.
   Добавить **все** нужные routes с точными methods из текущего Flask route map:
   UI `/`, `/static/*`, login/callback, MCP discovery/POST/OPTIONS, conversations,
   memory, file/vector stores, voice, runtime и все подключённые отделы.
   Не использовать непроверенное OpenAPI catch-all ради обхода списка.
2. Встроенное приложение уже отвечает за GitHub owner sign-in, `ALICE_SHORT_TOKEN`
   и MCP OAuth. Gateway by default пропускает запросы пользователю, знающему URL;
   application authentication обязана работать за прокси. Не ставить IAM auth
   или один статический Gateway API secret на браузерные/MCP пути: пользователь
   должен пройти app login, а shared browser secret станет публичным. Cloud IAM/
   API-key/OIDC policy шлюза применяется лишь к отдельному machine-to-machine API
   после отдельной проверки client flow и credentials.
3. `x-cloud-limit-count` ограничивает **суммарные** вызовы маршрута/хоста за окно,
   не квоту на Alice user. В starter лимит 120 chat запросов за 60 секунд — лишь
   консервативный пример, не измерение производительности и не справедливый
   per-user rate limit. Настроить отдельно для входа, MCP и тяжёлых операций после
   нагрузочного теста; проверять 429, нормальную нагрузку и общий эффект нескольких
   экземпляров. Server-side quota/auth остаются обязательными.
4. `x-cloud-log-group` принимает UUID log group. В starter он указан только для
   безопасного health endpoint. Прежде чем включать для чата/MCP/owner routes,
   выяснить, какие HTTP headers/path/query/body реально пишет Logging service,
   отключить чувствительные поля и проверить тестовыми секретными маркерами.
   Redaction после записи не исправляет утечку. Приложение обязано отдельно
   очищать stdout/trace согласно #478.
5. Кэширование не включать для любого Alice endpoint: ответы пользователя,
   OAuth, MCP, SSE и streaming персональны либо зависят от состояния. Ни cookies,
   ни Authorization не делают общий proxy cache безопасным автоматически.
6. Подсоединить домен из Cloud DNS к системному адресу Gateway и включить
   сертификат из Certificate Manager по `dns/README.md`. Добавить конечный URL
   в GitHub OAuth App/callback и MCP metadata. Container Apps продолжит иметь
   собственный provider URL; оценить его доступность напрямую. Протестировать,
   что прямой URL не обходит необходимые gateway лимиты и логи. Не считать
   приложение private, пока backend policy/ingress и отрицательный тест это
   не доказали.
7. Проверить login/callback, `/api/auth/me`, `/mcp` GET/POST/OPTIONS и discovery,
   browser static assets из вложенных директорий, chat, file upload/download,
   voice, CORS/preflight, request IDs, long responses и disconnect behavior.
   Gateway timeout/read timeout и buffering могут нарушить потоковый ответ.
   Не переключать DNS production до завершения этих тестов.

### Как различать Gateway auth и Alice users

API Gateway user/key policy отвечает за доступ к самому gateway. IAM authenticates
Cloud.ru organization/project users; Gateway static key identifies an API client;
OIDC connects an external identity provider. Alice user table, GitHub immutable
account ID, session cookie/short token and MCP OAuth continue enforcing identity
and ownership in the application. Эти слои не взаимозаменяемы.

Пример `/api/chat` применяет только request limit; он не отключает собственный
Alice auth. `/healthz` может быть публичным, не должен выдавать конфигурацию/
DB metadata. Проверить rate/response logs перед включением других routes.

Источники:
- [OpenAPI `x-cloud-*` specification](https://cloud.ru/docs/api-gateway-svp/ug/topics/concepts__apigw-extensions)
- [Gateway policies and auth](https://cloud.ru/docs/api-gateway-svp/ug/topics/guides__configure-policy)
- [Gateway custom domain](https://cloud.ru/docs/api-gateway-svp/ug/topics/guides__api-gateway__managedomains)
- [Container Apps integration](https://cloud.ru/docs/api-gateway-svp/ug/topics/concepts__api-gateway)
- [Container Apps request logging](https://cloud.ru/docs/container-apps-evolution/ug/topics/guides__container-create)

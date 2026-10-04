# Cloud DNS для API Gateway

Наши зоны находятся в Evolution DNS: `https://dns.api.cloud.ru`.
`https://console.cloud.ru/api/clouddns` — отдельный классический Cloud DNS.
У Evolution API Gateway нет публичного API управления (только консоль), поэтому
Chrome MCP работает на адресе контейнера без Gateway. Запросы требуют
Evolution IAM bearer token. Токен берётся штатным IAM service-account flow и
истекает через час. Для Cloud DNS получи Service Instance ID `parentId` в разделе
зон. Не подставляй Cloud project UUID вместо DNS service instance UUID.

Примеры выше — только body официального Cloud DNS API:

- Создание зоны: `POST /v1/zones`, `zone.example.json` (`name`, `parentId`).
- Список зон: `GET /v1/zones?parentId=...`; проверить `id`, `valid`, `delegated`.
- CNAME: `POST /v1/zones/{zoneId}/records`, `gateway-cname.example.json`
  (`name`, `type`, `ttl`, `values`). Проверь схему в актуальной спецификации
  Cloud DNS, доступной по API docs, перед отправкой. Значение — системный домен
  целевого Gateway `<gateway-uuid>.apigw.cloud.ru`.

Последовательность для custom domain:

1. Установить из инвентаризации, кому принадлежит домен, где сейчас его DNS,
   NS, A/AAAA/CNAME, MX, SPF/DKIM/DMARC, CAA, TXT verification и TTL. Не менять
   nameserver delegation и не перезаписывать зону вслепую.
2. Если Cloud DNS будет authoritative, создать зону, перенести и сверить все
   текущие записи, затем делегировать у регистратора. Указанные в актуальном
   Quick Start NS проверить непосредственно в Cloud Console; не брать их из
   старого примера или памяти.
3. Создать Shared API Gateway и узнать его UUID/system domain.
4. Добавить собственный `www.example.com` домен в API Gateway, выбрать сертификат
   Certificate Manager и добавить требуемый этим шлюзом CNAME. Cloud.ru описывает
   проверочную запись `<domain> CNAME <gateway-uuid>.apigw.cloud.ru`.
5. Дождаться validation, проверить DNS авторитетным resolver, TLS chain/SAN,
   статус домена и каждый URL. Только затем менять рабочий endpoint Алисы.

Container Apps не поддерживает собственный домен. DNS направляет custom name
на API Gateway; Gateway проксирует на Container Apps. Для apex/root домена не
подменяй A/ALIAS target на IP из случайного DNS lookup: API Gateway docs дают
CNAME на системный домен. Выбери поддомен, если registrar/DNS не разрешает
подходящий alias на корне.

Создание/удаление зоны, изменение NS и записей — живые изменения маршрутизации.
Сначала inventory и полный diff. Сохранять прежние рабочие записи и проверять
DNS/HTTPS после изменения; секретов в TXT-записях не хранить.

Источники:
- [Cloud DNS: auth](https://cloud.ru/docs/clouddns/ug/topics/api-ref_authentication)
- [Cloud DNS: zones API](https://cloud.ru/docs/clouddns/ug/topics/api-ref_zone)
- [Cloud DNS: records API](https://cloud.ru/docs/clouddns/ug/topics/api-ref_resource-record)
- [API Gateway: CNAME proof](https://cloud.ru/docs/api-gateway-svp/ug/topics/guides__api-gateway_domain_cname)
- [API Gateway: custom domains](https://cloud.ru/docs/api-gateway-svp/ug/topics/guides__api-gateway__domain_create)
- [Container Apps custom-domain limitation](https://cloud.ru/docs/container-apps-evolution/ug/topics/faq__customer-domain)

# Деплой Alice Pro через Cloud.ru Workflow Studio

Production-деплой Alice Pro использует Cloud.ru Workflow Studio → Services.
Cloud.ru получает код из публичного репозитория GitHub, собирает Docker-образ
по корневому `Dockerfile` и разворачивает приложение в Container Apps.

GitHub Actions не собирает образ и не загружает его в Artifact Registry. Он
только вызывает Public API Cloud.ru после изменения `master`, чтобы Cloud.ru
запустил новый деплой из своей настроенной GitHub-ветки.

## Одноразовая настройка в Cloud.ru

1. Откройте Workflow Studio → Services → Мои сервисы → Создать сервис.
2. Назовите сервис `alice-pro`.
3. В качестве источника укажите:
   `https://github.com/maksimp6/Chat.git`.
4. Выберите ветку `master`.
5. Укажите порт приложения `8080`, публичный адрес и масштабирование
   `0..1`, затем создайте сервис.
6. В настройках сервиса задайте только production-переменные Alice Pro.
   Секреты не добавляются в репозиторий, workflow или обычные логи.

После создания сохраните идентификатор приложения Cloud.ru в защищённом
хранилище GitHub Actions:

| Объект | Имя | Значение |
| --- | --- | --- |
| Actions variable | `CLOUDRU_WORKFLOW_APP_ID` | ID приложения Workflow Studio |
| Actions variable | `CLOUDRU_PROJECT_ID` | ID проекта Cloud.ru |
| Actions variable, опционально | `CLOUDRU_WORKFLOW_API_URL` | `https://pipeline.cloud.ru/public-api/v1` |
| Environment secret `production` | `CLOUDRU_WORKFLOW_API_KEY` | API-ключ Workflow Studio |

API-ключ не должен попадать в чат, файлы, issue, PR или вывод команд.

## Как идёт обновление

После merge в `master` запускается
`.github/workflows/cloudru-deploy.yml`. Workflow не делает checkout и не
запускает Docker. Он отправляет Cloud.ru команду запуска деплоя, после чего
Cloud.ru сам забирает `master` из GitHub, собирает приложение и обновляет
ревизию Container Apps.

До заполнения `CLOUDRU_WORKFLOW_APP_ID` и `CLOUDRU_WORKFLOW_API_KEY` push в
`master` завершается безопасным предупреждением без создания ресурсов и без
попытки деплоя. Ручной `workflow_dispatch` с незаполненной конфигурацией
завершается ошибкой.

## Ограничения текущего production-перехода

- Репозиторий `maksimp6/Chat` публичный, поэтому Cloud.ru может читать его по
  HTTPS без GitHub-токена.
- В Dockerfile приложение слушает `0.0.0.0:8080` и имеет `/healthz`.
- Для настоящего production всё ещё нужен поддерживаемый источник
  `ALICE_DATABASE_URL` с PostgreSQL. Cloud.ru Workflow Studio заменяет способ
  доставки кода, но не решает вопрос durable database.

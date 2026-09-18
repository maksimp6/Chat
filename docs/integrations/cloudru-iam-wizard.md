# Мастер API-ключей Cloud.ru

Alice Pro предоставляет backend- и UI-мастер для выпуска статических API-ключей Cloud.ru через IAM.

## Требования

Cloud.ru выпускает такие API-ключи для сервисных аккаунтов. Для управления API-ключами нужна соответствующая административная роль проекта. При создании задаются имя, описание, сервисы, IP-ограничения, срок действия и интервалы работы.

Официальная документация Cloud.ru: https://cloud.ru/docs/console_api/ug/topics/guides__static-api-keys__create

## Авторизация

Мастер использует пару ключа сервисного аккаунта для получения короткоживущего IAM bearer token через `/api/v1/auth/token`. Полученный токен используется только на backend при обращении к IAM API.

Секреты пары `CLOUDRU_IAM_KEY_ID` / `CLOUDRU_IAM_KEY_SECRET` и админский токен мастера не должны попадать в frontend storage, trace или обычные логи.

## Конфигурация

```text
CLOUDRU_IAM_WIZARD_ENABLED=false
CLOUDRU_IAM_WIZARD_TOKEN=<server-side-admin-token>
CLOUDRU_IAM_ENDPOINT=https://iam.api.cloud.ru
CLOUDRU_IAM_KEY_ID=<service-account-key-id>
CLOUDRU_IAM_KEY_SECRET=<service-account-key-secret>
```

Без `CLOUDRU_IAM_WIZARD_TOKEN` backend разрешает мастер только с loopback. Для Cloud.ru deployment необходимо задать отдельный случайный токен и вводить его в wizard при необходимости.

## Выпуск

POST `/api/cloudru/iam/api-keys` требует явного `confirm=true`. После создания секрет Cloud.ru сохраняется через `key_manager.py` в зашифрованном виде и возвращается UI один раз для немедленного сохранения пользователем.

Списки и метаданные API-ключей не содержат Key Secret. Cloud.ru также указывает, что секрет после закрытия окна создания повторно получить нельзя.

## Безопасность

- используйте минимально необходимый набор сервисов;
- ограничивайте IP и срок действия, когда это возможно;
- не храните секрет в localStorage;
- не отправляйте секрет в ExecutionTrace;
- храните encryption key менеджера вне репозитория;
- при компрометации ключ перевыпускайте/отзывайте через IAM.

Официальная документация Cloud.ru по списку API-ключей: https://cloud.ru/docs/console_api/ug/topics/guides__static-api-keys__get-list
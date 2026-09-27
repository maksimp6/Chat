# Host-managed preview validation

Preview проверяется через уже работающий Alice Pro host. Workflow не должен
создавать отдельный контейнер, Flask-процесс, localhost-прокси или runtime-порт
для каждой ветки.

Источники: [workflow](../.github/workflows/preview-deploy.yml),
[клиент жизненного цикла](../deploy/preview/environment_lifecycle.sh),
[потоковый host](runtime/threaded-environment-host.md).

## Что уже вошло в код

Workflow из [#359](https://github.com/maksimp6/Chat/pull/359) и runtime-фундамент
[#363](https://github.com/maksimp6/Chat/pull/363), включающий #347/#349, уже слиты.
Это не подтверждает, что соответствующая версия развернута на публичном host.
Проверенная база и известные блокеры находятся в
[реестре](integration/current-scope.md).

## Проверяемая последовательность

1. Workflow выбирает commit, выполняет checkout и предусмотренные проверки.
2. Клиент обращается к настроенному host и создаёт окружение для branch/SHA.
3. Host запускает управляемый runtime через `EnvironmentManager`.
4. Клиент проверяет `/environments/<id>/healthz` через gateway и фиксирует
   несекретные сведения об окружении и коммите.
5. Клиент запрашивает удаление окружения при выходе через `EXIT` trap. Ошибку
   очистки нельзя скрывать; аварийное прекращение runner требует проверки
   оставшихся окружений на host.

## Параметры Actions

В текущем workflow job связан с environment `production` и читает:

| Параметр | Назначение |
| --- | --- |
| `vars.ALICE_HOST_PREVIEW_ENABLED` | Job допускается только при строке `true` |
| `secrets.ALICE_ENVIRONMENT_HOST_URL` | Адрес host; при path-token аутентификации включает секретный префикс |
| `secrets.ALICE_ENVIRONMENT_API_TOKEN` | Дополнительный bearer credential, только если host поддерживает этот режим |
| `PREVIEW_COMMIT` | Для PR используется head SHA; иначе текущий код берёт `github.sha` |
| `PREVIEW_BRANCH` | Для PR head ref; иначе input `ref` либо ref запуска |

Нельзя печатать секретный host URL или помещать его в PR. Ключи production-БД,
провайдера, Android signing и SSH не должны передаваться коду preview-ревизии.

**Ограничение ручного запуска:** input `ref` сейчас влияет на `PREVIEW_BRANCH`,
но не заменяет `PREVIEW_COMMIT` на SHA этого input. Перед проверкой другой ветки
необходимо сверить фактическую пару branch/SHA; нельзя обещать, что произвольный
input автоматически приводит к checkout нужного коммита.

## Условия включения

Сначала подтвердите версию работающего host, доступность нужных credentials,
owner/runtime-границы, совпадение выбранного и разрешённого коммита, streaming
и очистку. Затем отдельно разрешайте включение gate или required deployment.
Этот документ сам по себе ничего не включает и не выполняет деплой.

`skipped` означает отсутствие выполненной проверки, а не успех. Даже успешный
`healthz` доказывает только проверенный маршрут и жизненный цикл. Текущий HTTP
путь использует приложение host, поэтому такой результат не доказывает работу
всех HTML/API/assets выбранной ветки. Для этого нужен отдельный проверяемый
revision-specific HTTP-контракт.

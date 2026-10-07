# Alice Dev: публичный OAuth origin и проверка подключения

Каноническая задача: #939; реализация первого исправления: #941.

## Конфигурация

`ALICE_DEV_PUBLIC_URL` задаётся deployment-кодом, не пользователем. Это не секрет.
Для commit-worker желаемый origin: `https://alice-dev-<project12>-<sha8>.containerapps.ru`.
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

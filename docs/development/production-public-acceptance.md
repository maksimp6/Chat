# Публичный baseline production (#938)

`python scripts/production_public_probe.py` выполняет два read-only запроса к
`https://maxxxpavlov.ru`: `/healthz` должен вернуть HTTP 200 и JSON `status=ok`,
корень без credentials должен вернуть 401. Redirect не принимается и не следует
автоматически. TLS-проверка включена; netrc, cookie предыдущей проверки и proxy
credentials не используются. HTTP без TLS разрешён только для loopback-тестов.

```bash
python scripts/production_public_probe.py > public-probe.json
cat public-probe.json
```

Код выхода 0 означает **только** успешный public HTTP baseline; 1 означает ошибку
HTTP/транспорта/контракта; 2 означает недопустимый origin. В JSON `accepted=false`
остаётся всегда: этот первый срез не проверяет authenticated UI/API или браузер.
Источник недоступен не означает, что endpoint отсутствует. Данные response body,
заголовки авторизации и произвольные сообщения сетевых исключений не публикуются.

Probe использует socket connect/read timeouts 3/5 секунд и ограничивает health body
16 КиБ. Для автоматического запуска дополнительно задавать внешний wall-clock
лимит, например `timeout 30s python scripts/production_public_probe.py`; socket
read timeout не является общим deadline всей проверки.

Regression suite поднимает настоящий локальный HTTP-сервер и проверяет правильный
baseline, ошибочные HTTP-ответы, отказ от redirect, неправильный/слишком большой
health JSON и отсутствие утечек. Ошибки TLS/тайм-аута/сети также проверяются
детерминированно. Запуск: `python -m pytest -q tests/test_production_deploy_probe.py`.

Дальнейший acceptance #938: доступный DNS/TLS, авторизованный безопасный GET/UI,
настоящий браузерный проход и подтверждение видимого результата. Успех тестов
самого probe не заменяет ни один live gate.

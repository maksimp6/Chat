# Исходящие запросы через контракты

Поручение владельца: «тогда теперь все запросы к апи через них».
Канонические [#1567](https://github.com/maksimp6/Chat/issues/1567) и
[PR #1569](https://github.com/maksimp6/Chat/pull/1569).

`api/yandex_transport.py` сопоставляет HTTP-метод и URL с именованным контрактом,
проверяет path/query/body и только затем вызывает транспорт. Неизвестный маршрут,
поле, тип, неподдерживаемая схема или некорректная кодировка останавливают отправку.
Ошибки не содержат отклонённых значений, текста, аудио или credentials.

## Охват проверенных точек отправки

| Область | Точки, использующие границу |
| --- | --- |
| Основной клиент | `request_mixin`, `conversations`, `polling_mixin`, `response_poller` |
| SDK | `yandex_client_modules/sdk.py` |
| Files / Vector Store | `yandex_client_modules/files.py`, `scripts/semantic_pipeline.py` |
| Каталог моделей | `provider_accounts/model_discovery.py` |
| Управление API-ключами | `yandex_client_modules/api_key_provider.py` |
| Извлечение памяти | `agent_memory/extractor.py` |
| SpeechKit | STT/TTS в `chat/voice_routes.py` |
| Runner-модель | Semantic pipeline, Q14 review/observer, PR Court |
| Embeddings | `scripts/rag_pipeline.py` |
| Старый отправитель логов | `scripts/send_logs.py`: незадокументированный URL блокируется |

Проверены Python-источники и workflow entrypoints. Исполняемых gRPC/WebSocket
клиентов Yandex в текущем коде не найдено: справочные схемы этих протоколов сами
по себе не создают транспорт. Не относящиеся к Yandex вызовы GitHub в смешанных
runner helpers сохраняют собственный транспорт. PR Court определяет необходимость
проверки также по фактическому URL, независимо от флага авторизации `provider`.

## Какие контракты применяются

Полный неизменённый `catalog.json` остаётся контрактом исторического снимка.
Для реально используемых форм запроса предусмотрены 22 явных клиентских профиля
в `api/yandex_request_profiles.py`, с именами `yandex.runtime.*`.
Они имеют собственные `source.url`, дату проверки **24.10.2026** и строгие schemas.
Приоритет профиля определён явно; оставшиеся методы используют каталог снимка.
Неполный метод без профиля не отправляется.

Профили покрывают Responses/continuations/polling, создание conversations,
Files/Vector Store/search, обычный completion для памяти, IAM create/delete и
SpeechKit STT/TTS. Это проверенная клиентская часть API: неизвестные возможности
и разновидности объектов отклоняются до добавления явного контракта. Они не
объявляют полную поддержку всей спецификации поставщика или конкретной моделью.

Основные первоисточники:

- [Responses create](https://aistudio.yandex.ru/en/docs/ai-studio/api/Responses/createResponse)
  и [get](https://aistudio.yandex.ru/en/docs/ai-studio/api/Responses/getResponse).
- [Vector Store search](https://aistudio.yandex.ru/en/docs/ai-studio/api/Vector-stores/searchVectorStore)
  и [добавление файла](https://aistudio.yandex.ru/en/docs/ai-studio/api/Vector-stores/createVectorStoreFile).
- [SpeechKit STT](https://aistudio.yandex.ru/en/docs/speechkit/stt/api/request-api)
  и [TTS](https://aistudio.yandex.ru/en/docs/speechkit/tts/request).
- [IAM create](https://yandex.cloud/en/docs/iam/api-ref/ApiKey/create)
  и [delete](https://yandex.cloud/en/docs/iam/api-ref/ApiKey/delete).
- Для completion и остальных методов также доступны связанные неизменённые
  исходные страницы в репозиторном снимке; ссылки указаны в каждом профиле.

## Передача и ограничения

- `requests`: проверяется отделённая копия JSON/params/form, редиректы запрещены;
  ответ 3xx становится явным отказом без второй отправки.
- `urllib`: проверяется готовый `Request`, затем используется opener без redirects.
- `http.client`: проверяются готовые bytes и конечный host до `connection.request`.
- Настроенный AI Studio proxy должен явно совпадать с переданным `base_url`;
  сторонний origin, HTTP, userinfo и выход из базового пути отклоняются.
- JSON `null` не подменяется пустым объектом; повторяющиеся scalar query и
  path traversal отклоняются. Integer/boolean query восстанавливаются из wire
  представления только согласно явной схеме.
- Multipart: только документированные поля и один файл, размер 1..128 MiB.
  Размер stream проверяется через seek/tell с восстановлением позиции; неизвестный
  размер без чтения блокируется. Для готового multipart проверяются parts/boundary.
- STT: бинарные данные 1..1 MiB и явная query schema. Дополнительные требования
  к длительности, кодеку и каналам не доказываются одной длиной bytes; существующая
  WAV-проверка маршрута сохранена, провайдер остаётся границей декодирования.
- TTS: проверяется URL-encoded размер до 15 KiB и типы формы.
- Для static chunking проверяется также overlap ≤ половины chunk size.
- В основном запросе первая проверка предшествует резервированию квоты и receipt;
  повторная проверка после request hook и перед транспортом защищает окончательный
  payload. Trace IDs, callbacks, сроки и обработка ответов не заменяются контрактами.
- Отказ контракта не считается временным сетевым сбоем и не повторяется polling.

Валидация входящих ответов и ошибок остаётся отдельным этапом. Обёртки сохраняют
существующую обработку HTTP/ответов; не заявляется, что мартовские response schemas
стали полными. Этот этап не выполняет merge, deploy или платные проверки провайдера.

## Проверки

```bash
python -m pytest -q tests/test_yandex_transport_contracts.py
python scripts/build_yandex_contracts.py --check
```

Тесты используют настоящие registry/profile schemas. Подменяются только конечные
транспорты. Есть контроль реального Requests PreparedRequest, положительная отправка
и отрицательные сценарии с **нулём отправок**, сохранение stream position, raw JSON
и multipart, redirects, host mismatch, query/path/body, размеры, quotas/receipt
ordering и архитектурный контроль прямых обходов. Тестовые transport doubles
принимают и проверяют `allow_redirects=False`, валидатор в них не отключается.

Живой вызов API/модели и пользовательская приёмка не выводятся из этих offline
проверок. Результаты конкретного опубликованного HEAD и CI записываются в PR.

## Среды исполнения

Только сетевая часть standalone runner загружает контрактный валидатор; retrieval,
офлайн Q14 и чтение локальных свидетельств импортируются без site-packages.
Минимальная зависимость объявлена в `contracts/yandex/requirements.txt`, включена
в основные и Android requirements, устанавливается runner workflows перед API.
Docker копирует этот файл до установки зависимостей. Android staging переносит
настоящий `catalog.json` вместе с Python-кодом; byte identity проверяется тестом.
Это проверка подготовки данных, а не сборка/установка APK или подтверждение
Android ABI зависимостей. Gradle не запускается.

Отдельный workflow `Yandex API contracts` запускает профильный pytest на точном
HEAD PR без credentials; существующая политика отчётности job сохранена дословно.
Реальный результат pytest читается в логе отдельно от итоговой policy-отметки.

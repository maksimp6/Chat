# Logos - API Documentation

## Реализованные функции

### 1. Responses API (Текстовый чат)

**Эндпоинт:** `POST /api/chat`
**Описание:** Отправка текстовых сообщений и получение ответов от LLM
**Параметры:**

- `message` (string, required): Текст сообщения
- `conversation_id` (string, optional): ID диалога
- `model` (string, optional): Модель (по умолчанию: logos-runtime)

**Пример:**

```bash
curl -X POST http://localhost:8080/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"Привет","model":"logos-runtime"}'
```

#### Живой ответ, повтор и `request_id`

Запрос без `request_id` и без `Accept: text/event-stream` работает как раньше: один JSON-ответ с прежними полями и кодами (200, 400, 404, 409, 429, 500, 502).

- `request_id` (string, optional): `^[A-Za-z0-9_-]{16,128}$`. Повтор запроса с тем же `request_id` в том же диалоге не запускает ход второй раз. Неверный формат — `400 invalid_params`.
- `params.stream` (boolean): сервер просит у провайдера ответ потоком (SSE). Не зависит от заголовка `Accept`.
- `Accept: text/event-stream`: ответ приходит живым потоком событий, пока модель отвечает.

Ответы на живой запрос:

- исход решён до сохранения вопроса (ошибки 400/404/409/503, `invalid_skill_selection`, ошибка 500 до записи вопроса): прежний JSON и прежний код, без SSE;
- вопрос принят: `200`, `Content-Type: text/event-stream; charset=utf-8`, `Cache-Control: no-store`, `X-Accel-Buffering: no`; первое событие `turn`, последнее `done`.

Повтор того же `request_id`:

| Состояние | Ответ |
| --- | --- |
| тот же запрос, ход идёт в этом процессе, запрошен SSE | поток событий с начала (с события 1) |
| тот же запрос, ход идёт, но поток недоступен (JSON-запрос или ход начат JSON-запросом) | `202 {"status":"running","request_id","conversation_id","user_message_id","invocation_id"}` |
| тот же запрос, ход завершён, ответ сохранён | `200 {"status":"stored","request_id","conversation_id","user_message_id","invocation_id","message_id","http_status"}` |
| тот же запрос, ход завершён, ответ не сохранён | `500 turn_failed` (`status: "failed"`, `request_id`, `conversation_id`, `user_message_id`, `invocation_id`) |
| тот же запрос, ход прервался после сохранения вопроса (перезапуск сервера или сбой при сохранении итога) | `409 turn_lost` (`status: "lost"`, `request_id`, `conversation_id`, `user_message_id`, `invocation_id`) |
| тот же запрос, процесс сервера перезапустился до сохранения вопроса | GET: `409 turn_lost` с `user_message_id: null` (вопрос не сохранён, отправьте его снова); POST: ход запускается заново |
| тот же запрос, сбой при сохранении итога в этом процессе, вопрос не сохранён | POST и GET: `409 turn_lost` с `user_message_id: null`; следующий POST запускает ход |
| другой текст, модель, число фото или `retry_message_id` | `409 turn_request_conflict` |
| хранилище не смогло проверить запрос или записать квитанцию, вопрос не сохранён | `503 turn_store_unavailable` (`request_id`, `detail`); ход не запущен, запрос можно отправить снова |
| диалог не найден или недоступен | `404 conversation_not_found` |
| нет `conversation_id` | `400` с `error: "conversation_id is required"` |
| ход запущен этим запросом | те же коды, что без `request_id` (200, 400, 404, 409, 429, 500, 502), и SSE при `Accept: text/event-stream` |

Формат SSE: каждый кадр `id: <n>\ndata: <JSON одной строкой>\n\n`, `n` — номер события в журнале хода (с 1). Строк `event:` нет, тип события — поле `type`. Подряд идущие `delta` (и `reasoning` с одинаковым `summary`) могут прийти одним кадром с id последнего. Через 15 с без событий приходит `: keep-alive`. Поток без `done` прерван.

| `type` | Поля | Смысл |
| --- | --- | --- |
| `turn` | `request_id`, `conversation_id`, `user_message_id`, `invocation_id`, `session_id`, `trace_id`, `title`, `model` | вопрос сохранён; браузер больше не отправляет этот ход POST-запросом |
| `status` | `phase`, `text` | строка состояния; `phase`: `sending`, `queued`, `generating`, `reasoning`, `writing`, `continuation`, `resuming`, `polling`, `finalizing` (статуса с `phase` `tool` нет: ход инструмента приходит событием `tool`) |
| `round` | `round`, `kind` (`initial`, `tool`, `output_limit`), `part` | начался новый запрос к провайдеру; `part` — номер продолжения для `output_limit`, иначе 0 |
| `delta` | `text` | кусок текста ответа текущего запроса |
| `reasoning` | `text`, `summary` | кусок хода мыслей модели |
| `round_text` | `text`, `reasoning` | итоговый текст запроса заменяет показанный (например, после перехода на опрос) |
| `tool` | `phase` (`requested`, `start`, `done`, `failed`), `kind`, `name`, `server_label`, `text` | ход инструмента |
| `retry` | `operation` (`post`, `poll`, `stream`, `conversation`), `attempt`, `reason`, `status_code`, `delay_ms`, `text` | автоматический повтор запроса к провайдеру |
| `done` | `http_status`, `body` | конец хода; `body` — тот JSON, который вернул бы обычный запрос, но у сохранённого ответа (`200` с `message_id`) поле `trace` — ссылка `{"trace_id", "invocation_id", "storage": "invocation"}`, как у сохранённого сообщения; полная трасса — `GET /api/invocations/<invocation_id>/trace` |

Отключение браузера или «Стоп» не останавливает ход: сервер дожидается ответа модели, сохраняет его, проводит оплату и записывает итог.

**Эндпоинт:** `GET /api/chat/turns/<request_id>/events?conversation_id=<id>`
**Описание:** повторное подключение к ходу, начатому с этим `request_id`.

- Позиция: заголовок `Last-Event-ID: <n>` или параметр `after=<n>` (по умолчанию 0); приходят события начиная с `n+1`.
- Ход идёт в этом процессе: поток событий до `done`.
- Ход завершён с ответом: `200 "stored"`; завершён без ответа: `500 turn_failed`; прервался (перезапуск сервера или сбой при сохранении итога): `409 turn_lost`; ход идёт, но поток недоступен: `202 "running"`; запроса нет: `404 turn_request_not_found`.
- Неверный `request_id`: `400 invalid_params`; чужой или удалённый диалог: `404 conversation_not_found`.

Повторы на стороне сервера: запрос к провайдеру отправляется снова при HTTP 408, 425, 429 и 503, при ошибке соединения, возникшей до отправки запроса, и при ограничении частоты `rate_limit_exceeded` до номера ответа. Ответы с ошибкой, которые повтор не исправит (например, 400), и сбои после отправки, когда неизвестно, принят ли запрос (обрыв связи, 5xx кроме 503), не повторяются: платный запрос повторно не отправляется. Запросы возобновления и опроса уже отправленного ответа повторяются при временных сбоях, включая 5xx кроме 501; ответ 404 при опросе считается ещё не появившимся ответом и опрашивается снова. Квота запроса возвращается, только если провайдер заведомо не принял запрос: при сбое до отправки или при HTTP 429 (либо `rate_limit_exceeded`), не прошедшем после всех повторов; в остальных случаях квота остаётся занятой. После того как известен номер ответа, оборванный поток продолжается по номеру или ответ читается опросом; отказ провайдера в продолжении потока — ошибка с этим номером, опроса в этом случае нет. Пауза между повторами растёт случайно; предел паузы опроса — 3 с в первые 120 с ожидания и 15 с после них. Всё ограничено только сроком ожидания (`poll_timeout`).

#### История беседы на медленном канале

Канал до 10 kb/s (1,25 КБ/с) — рабочий случай: история не перекачивается целиком без нужды, и ни одно ожидание не зависит от её размера. Ответы сжимаются gzip, если клиент передал `Accept-Encoding: gzip`; JSON не экранирует кириллицу.

**Эндпоинт:** `GET /api/conversations/<id>/messages`

- Без параметров и без `Accept: application/x-ndjson`: вся история JSON-ом `{"messages": [...]}`, как раньше.
- `Accept: application/x-ndjson` (контракты `chat.conversation.messages.stream.*`): поток строк JSON. Первая строка — `{"count": N}` (сообщений в беседе). Затем сообщения от новых к старым, по одному в строке. Последняя строка — `{"end": true}`; поток без неё прерван. С `?before=<id>` поток начинается с сообщения старше `<id>` — так клиент продолжает после обрыва с самого старого полученного сообщения.
- `?after=<id>` (контракт `chat.conversation.messages.after.response`): `{"messages": [...], "count": N, "last": {...} | null}` — только сообщения новее `<id>`, число сообщений и последнее сообщение. Клиент, у которого уже есть история, читает только новое.
- Неверный `after` или `before` (не номер сообщения): `400 invalid_params`; чужой или удалённый диалог: `404 conversation_not_found`.

**Эндпоинт:** `GET /api/conversations/<id>/state` (контракт `chat.conversation.state.response`)
**Описание:** `{"count", "last_id", "last_role", "last_source"}`, около 100 байт: по нему ожидающий ход сравнивает, сохранён ли вопрос и ответ, вместо чтения истории.

Браузер ограничивает запрос временем до заголовков, а после них — только временем без единого байта: тело, которое идёт, дочитывается при любой скорости.

#### Загрузка кусками с докачкой

Файл и фото уходят кусками: обрыв связи стоит не больше одного куска, загрузка продолжается с того, что сервер уже принял (`workspace/upload_routes.py`, `static/upload.js`).

| Запрос | Ответ |
| --- | --- |
| `POST /api/uploads` `{"name", "size", "kind": "file" \| "image", "purpose"?}` | `201 {"id", "name", "kind", "received": 0, "size"}`; `kind: "file"` — только расширения Files; больше 128 МБ — `413` |
| `PUT /api/uploads/<id>`, заголовок `Upload-Offset: <received>`, тело — следующий кусок | `{"id", "name", "kind", "received", "size"}`; другое смещение — `409 offset_mismatch` с принятым размером; кусок за пределами размера — `413 piece_exceeds_size`; оборванный кусок — `400 piece_incomplete`, он не дописывается |
| `GET /api/uploads/<id>` | `{"id", "name", "kind", "received", "size"}` |
| `DELETE /api/uploads/<id>` | `{"id", "deleted": true}` |
| `POST /api/uploads/<id>/complete` | `kind: "file"`: `201` и тот же ответ, что у `POST /api/files`; повтор отвечает тем же. `kind: "image"`: `{"upload_id", "size"}`. Принято не всё — `409 upload_incomplete` |

Неизвестный `id` — `404 upload_not_found`. Кусок сначала пишется отдельно и дописывается, только если пришёл целиком и его смещение всё ещё равно принятому размеру. Браузер начинает с куска 1 KiB, дальше подбирает размер по измеренной скорости, при сбое делит пополам, но не меньше 1 KiB. Незавершённые и отправленные загрузки лежат в `LOGOS_UPLOAD_DIR` (по умолчанию `data/uploads`), пока их не удалят: срок хранения не задан.

Вложение хода `POST /api/chat`: `{"data_url": ...}` или `{"upload_id": ...}` — фото, загруженное кусками (`kind: "image"`, загрузка завершена). Браузер перед загрузкой перекодирует фото: длинная сторона до 1600 px, WebP (JPEG, если браузер не пишет WebP), качество 0,8; если результат не меньше оригинала, уходит оригинал.

### 2. Vision API (Анализ изображений)

**Эндпоинт:** `POST /api/vision`
**Описание:** Анализ изображений с помощью YandexGPT
**Параметры:**

- `image` (string, required): Base64-кодированное изображение
- `prompt` (string, optional): Запрос для анализа
- `model` (string, optional): Модель (по умолчанию: yandexgpt-5.1)

**Пример:**

```bash
curl -X POST http://localhost:8080/api/vision \
  -H "Content-Type: application/json" \
  -d '{"image":"base64data...","prompt":"Опиши изображение"}'
```

### 3. Embeddings API (Векторные представления)

**Эндпоинт:** `POST /api/embeddings`
**Описание:** Векторные представления текста через Yandex `textEmbedding`. Авторизация не требуется; запросы тратят серверный `YANDEX_API_KEY`.
**Параметры:**

- `text` (string): один текст. Ответ содержит `embedding`.
- `texts` (array of string): пакет до 64 текстов. Ответ содержит `embeddings`. Нельзя передавать `text` и `texts` вместе.
- `model` (необязательно): одна из `text-search-doc`, `text-search-query`, `text-embeddings-v2-doc`, `text-embeddings-v2-query`. Если не задана, выбирается по `mode`.
- `mode` (`doc` или `query`, по умолчанию `doc`): используется, только когда `model` не задана. Нельзя передавать `model` и `mode` вместе: ответ 400. `doc` — `text-search-doc`, `query` — `text-search-query`.

Каждый текст не длиннее 4000 символов (лимит токенов у Yandex в документации противоречив, см. `config/model_profiles/yandex-ultra-profiles.json`). Ответ также содержит `model`, `tokens` и `cost`. Стоимость считается по `EMBEDDING_PRICE_PER_1K_TOKENS`, если эта переменная задана; иначе `cost` показывает «неизвестно».

**Пример:**

```bash
curl -X POST http://localhost:8080/api/embeddings \
  -H "Content-Type: application/json" \
  -d '{"texts":["Привет мир"],"mode":"doc"}'
```

Коды ответа: 400 — неверный запрос, 503 — не заданы `YANDEX_API_KEY` или `YANDEX_PROJECT_ID`, 502 — сбой Yandex, 504 — превышен общий лимит времени. В ответах 502 и 504 есть `completed` (сколько текстов уже обработано) и `tokens_spent`.

### Completion API (отдельно от Responses)

**Эндпоинт:** `POST /api/completion`
**Описание:** Генерация через Yandex completion API для моделей с транспортом `text_generation_async` (сейчас только `yandexgpt-5.1`). Авторизация не требуется; запросы тратят серверный `YANDEX_API_KEY`.
**Параметры:**

- `message` (string) или `messages` (до 32 элементов с `role` = `system`, `user` или `assistant` и `text`). Нельзя передавать оба.
- `temperature` (0–1, по умолчанию 0.3 из профиля).
- `max_tokens` (целое от 1 до 32768, по умолчанию 32768 из профиля).

Ответ: `model`, `reply`, `usage`. Коды: 400 — неверный запрос, 502 — сбой Yandex, 503 — модель или ключ не настроены.

**Пример:**

```bash
curl -X POST http://localhost:8080/api/completion \
  -H "Content-Type: application/json" \
  -d '{"message":"Привет","max_tokens":200}'
```

### 4. TTS API (Синтез речи)

**Эндпоинт:** `POST /api/tts`
**Описание:** Преобразование текста в речь
**Параметры:**

- `text` (string, required): Текст для озвучки
- `voice` (string, optional): Голос (по умолчанию: filipp)
- `speed` (float, optional): Скорость речи (по умолчанию: 1.0)

**Пример:**

```bash
curl -X POST http://localhost:8080/api/tts \
  -H "Content-Type: application/json" \
  -d '{"text":"Привет мир","voice":"filipp"}'
```

### 5. Tokenizer API (Подсчёт токенов)

**Эндпоинт:** `POST /api/tokenize`
**Описание:** Подсчёт количества токенов в тексте
**Параметры:**

- `text` (string, required): Текст для токенизации
- `model` (string, optional): Модель токенизатора

**Пример:**

```bash
curl -X POST http://localhost:8080/api/tokenize \
  -H "Content-Type: application/json" \
  -d '{"text":"Привет мир"}'
```

### 6. Realtime API (Голосовой режим)

**Эндпоинты:**

- `POST /api/voice/session` - Создание сессии
- `POST /api/voice/audio` - Отправка аудио
- `GET /api/voice/events` - Получение событий (SSE)
- `POST /api/voice/close` - Закрытие сессии

**Описание:** Реальное время голосового взаимодействия с LLM

## Планируемые функции

### 7. Search API (Семантический поиск)

Статус: В разработке

### 8. Classifiers API (Классификация)

Статус: В разработке

### 9. Moderation API (Модерация контента)

Статус: В разработке

### 10. Batch API (Пакетная обработка)

Статус: В разработке

## Использование в UI

### Загрузка изображений

Нажмите кнопку 📷 рядом с полем ввода, выберите изображение, и AI проанализирует его. Фото сжимается в браузере и загружается кусками ещё до отправки; на превью виден процент загрузки, отправка ждёт её окончания.

### Озвучка текста

В контекстном меню сообщения выберите "Озвучить" для воспроизведения текста.

### Подсчёт токенов

Используйте функцию `countTokens(text)` в консоли браузера для подсчёта токенов.

## Логирование

Все API-запросы логируются в:

- `logs/app.txt` - основные логи приложения
- `logs/api_debug.txt` - детали API-запросов

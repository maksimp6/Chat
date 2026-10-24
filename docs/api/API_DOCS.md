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
**Описание:** Векторные представления текста через Yandex `textEmbedding`. Нужен владелец: без него ответ 401.
**Параметры:**

- `text` (string): один текст. Ответ содержит `embedding`.
- `texts` (array of string): пакет до 64 текстов. Ответ содержит `embeddings`. Нельзя передавать `text` и `texts` вместе.
- `model` (необязательно): одна из `text-search-doc`, `text-search-query`, `text-embeddings-v2-doc`, `text-embeddings-v2-query`. Если не задана, выбирается по `mode`.
- `mode` (`doc` или `query`, по умолчанию `doc`): используется, только когда `model` не задана. `doc` — `text-search-doc`, `query` — `text-search-query`.

Каждый текст не длиннее 4000 символов (лимит токенов у Yandex в документации противоречив, см. `config/model_profiles/yandex-ultra-profiles.json`). Ответ также содержит `model`, `tokens` и `cost`. Стоимость считается по `EMBEDDING_PRICE_PER_1K_TOKENS`, если эта переменная задана; иначе `cost` показывает «неизвестно».

**Пример:**

```bash
curl -X POST http://localhost:8080/api/embeddings \
  -H "Content-Type: application/json" \
  -d '{"texts":["Привет мир"],"mode":"doc"}'
```

Коды ответа: 400 — неверный запрос, 401 — нет владельца, 503 — не заданы `YANDEX_API_KEY` или `YANDEX_PROJECT_ID`, 502 — сбой Yandex.

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

Нажмите кнопку 📷 рядом с полем ввода, выберите изображение, и AI проанализирует его.

### Озвучка текста

В контекстном меню сообщения выберите "Озвучить" для воспроизведения текста.

### Подсчёт токенов

Используйте функцию `countTokens(text)` в консоли браузера для подсчёта токенов.

## Логирование

Все API-запросы логируются в:

- `logs/app.txt` - основные логи приложения
- `logs/api_debug.txt` - детали API-запросов

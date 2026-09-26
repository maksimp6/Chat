# Alice Pro - API Documentation

## Реализованные функции

### 1. Responses API (Текстовый чат)

**Эндпоинт:** `POST /api/chat`
**Описание:** Отправка текстовых сообщений и получение ответов от LLM
**Параметры:**

- `message` (string, required): Текст сообщения
- `conversation_id` (string, optional): ID диалога
- `model` (string, optional): Модель (по умолчанию: aliceai-llm)

**Пример:**

```bash
curl -X POST http://localhost:8080/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"Привет","model":"aliceai-llm"}'
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
**Описание:** Получение векторного представления текста для семантического поиска
**Параметры:**

- `text` (string, required): Текст для векторизации

**Пример:**

```bash
curl -X POST http://localhost:8080/api/embeddings \
  -H "Content-Type: application/json" \
  -d '{"text":"Привет мир"}'
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

Нажмите кнопку 📷 рядом с полем ввода, выберите изображение, и AI проанализирует его.

### Озвучка текста

В контекстном меню сообщения выберите "Озвучить" для воспроизведения текста.

### Подсчёт токенов

Используйте функцию `countTokens(text)` в консоли браузера для подсчёта токенов.

## Логирование

Все API-запросы логируются в:

- `logs/app.txt` - основные логи приложения
- `logs/api_debug.txt` - детали API-запросов

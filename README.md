# Alice Pro — AI-ассистент с MCP-инструментами

Веб-приложение чата с Yandex AI Studio, поддержкой MCP-инструментов (Git-репозитории), Conversations API и PWA.

## 🚀 Быстрый старт

### 1. Подготовка окружения
```bash
python3 -m venv venv
source venv/bin/activate
```

### 2. Установка зависимостей
```bash
pip install flask requests python-dotenv
```

### 3. Настройка конфигурации
Создайте файл `.env`:
```env
YC_API_KEY=AQVNyejOloTFOGUuRm5ytNxYVM4O1FAQikHlLCB-
YC_PROJECT_ID=b1g1fekh2198nuan1tnh
YC_BASE_URL=https://ai.api.cloud.yandex.net/v1
HOST=0.0.0.0
PORT=8080
SECRET_KEY=change_this_to_a_random_secret_string
```

### 4. Запуск приложения
```bash
python app.py
```

### 5. Открытие в браузере
Перейдите по адресу: [http://localhost:8080](http://localhost:8080)

---

## 📁 Структура проекта

```text
alice_pro/
│
── app.py                      # Flask-приложение (main entry point)
│   ├── GET  /                              # Главная страница
│   ├── GET  /manifest.json                 # PWA manifest
│   ├── GET  /favicon.ico                   # Favicon
│   ├── GET  /static/<path>                 # Статические файлы (CSS, JS)
│   ├── POST /api/conversations             # Создание диалога в Yandex Cloud
│   ├── GET  /api/conversations/<id>/history # Получение истории диалога
│   ├── POST /api/chat                      # Отправка сообщения в AI (background mode)
│   └── POST /api/reset                     # Сброс сессии
│
├── config.py                   # Конфигурация
│   ├── YC_API_KEY              # API-ключ Yandex Cloud
│   ├── YC_BASE_URL             # Базовый URL API
│   ├── YC_PROJECT_ID           # ID проекта Yandex Cloud
│   ├── MODEL                   # Модель: gpt://{PROJECT_ID}/aliceai-llm/latest
│   ├── INSTRUCTIONS            # Системный промпт
│   ├── MCP_TOOLS               # Список MCP-серверов
│   ├── HOST, PORT              # Настройки веб-сервера
│   └── SECRET_KEY              # Секретный ключ Flask
│
├── yandex_client.py            # Клиент Yandex Cloud API
│   ├── YandexResponsesClient   # Основной класс
│   ├── create_conversation()   # POST /v1/conversations
│   ├── get_conversation_history() # GET /v1/conversations/{id}/items
│   ├── ask_background()        # POST /v1/responses (background=True) → task_id
│   ├── get_response_status()   # GET /v1/responses/{id} → статус задачи
│   ├── wait_for_response()     # Polling статуса до completed/failed/cancelled
│   ├── ask()                   # ask_background + wait_for_response
│   ├── extract_text()          # Извлечение текста из ответа
│   └── extract_tool_calls()    # Извлечение MCP-вызовов
│
├── templates/
│   └── index.html              # HTML-структура (подключает CSS и JS)
│
├── static/
│   ├── style.css               # Все стили (CSS variables, темы, адаптивность)
│   ├── app.js                  # Вся логика (sidebar, chat, search, theme)
│   └── manifest.json           # PWA manifest
│
├── debug_response.json         # Последний полный ответ от Yandex API
├── history_debug.json          # Лог запросов истории (JSON-формат)
├── app.log                     # Обычный текстовый лог приложения
├── .gitignore                  # Игнорирование debug файлов
└── README.md                   # Этот файл
```

## 🔑 Конфигурация

| Переменная | Описание | Пример |
|---|---|---|
| `YC_API_KEY` | API-ключ Yandex Cloud | `AQVNyejOloTFO...` |
| `YC_PROJECT_ID` | ID проекта | `b1g1fekh2198nuan1tnh` |
| `YC_BASE_URL` | Базовый URL API | `https://ai.api.cloud.yandex.net/v1` |
| `HOST` | Хост сервера | `0.0.0.0` |
| `PORT` | Порт сервера | `8080` |

## 🧠 Как работает контекст диалога

1. **Создание диалога**: `POST /v1/conversations` → получаем `conversation_id`
2. **Отправка сообщения**: `POST /v1/responses` с параметром `"conversation": "<id>"` и `"background": true`
3. **Получение истории**: `GET /v1/conversations/{id}/items?order=asc`

**Важно**: параметр называется `"conversation"`, а не `"conversation_id"`.

## ⚙️ Фоновый режим (Background Mode)

Все запросы к Yandex AI Studio отправляются в фоновом режиме (`background=True`):

1. **Отправка**: `POST /v1/responses` с `background=True` → API сразу возвращает `response_id`
2. **Polling**: бэкенд опрашивает `GET /v1/responses/{response_id}` каждые 2 секунды
3. **Статусы задачи**:
   - `queued` — задание в очереди
   - `in_progress` — выполняется
   - `completed` — готово (возвращаем результат)
   - `failed` / `cancelled` — ошибка (возвращаем ошибку клиенту)
4. **Timeout**: максимальное время ожидания — 300 секунд (5 минут)

**Преимущества**:
- Избавление от таймаутов Yandex API при долгих MCP-операциях (git clone, большие diff)
- Надежная обработка объемных задач генерации
- Прозрачность для клиента (HTTP-соединение держится до готовности ответа)

##  MCP-инструменты

| Label | Описание |
|---|---|
| `reposit` | Git-репозитории (list, create, diff, merge requests, discussions, blobs, branches, tags) |
| `a` | Дополнительный MCP-сервер |

## ✨ Фичи фронтенда

- **Тёмная/светлая тема** — кнопка 🌙/☀️ в шапке
- **Поиск по диалогам** — поле в сайдбаре + `Ctrl/Cmd+K`
- **Переименование диалогов** — кнопка ✏️, inline-редактирование
- **Удаление диалогов** — кнопка × с подтверждением
- **Кнопки прокрутки** — плавающие кнопки ↑/↓
- **Markdown** — `**жирный**`, `*курсив*`, `` `код` ``, блоки кода, списки
- **Адаптивность** — `100dvh`, учёт safe-area
- **PWA** — установка на главный экран
- **Разделение кода** — HTML, CSS, JS в отдельных файлах

##  Отладочные файлы

- **`debug_response.json`** — полный JSON последнего ответа от Yandex API
- **`history_debug.json`** — лог запросов истории (последние 100 записей)
- **`app.log`** — обычный текстовый лог приложения

## 📄 Лицензия

MIT

---
*README актуализирован: 2026-09-01*

# Общая архитектура проекта Logos

## Высокоуровневая схема

```
Пользователь
  │
  ▼
Браузер
  │
  ▼
Flask / app.py
  │
  ├──────────────► Kristall / storage/db.py
  │
  ├──────────────► Yandex AI Studio
  │               │
  │               ▼
  │          AI Responses API
  │
  ├──────────────► MCP
  │               │
  │               ├── filesystem
  │               ├── git
  │               └── другие инструменты
  │
  └──────────────► Files / Vector Stores
```

## Основные компоненты

### 1. Веб‑интерфейс (static/)
* `chat.js` — обработка чата и Markdown;
* `core.js` — основная логика фронтенда;
* `sidebar.js` — боковая панель;
* `models.js` — работа с моделями;
* `voice.js` — голосовые функции;
* `style.css` — стили.

### 2. Бэкенд (Flask)
* `app.py` — главный файл приложения;
* `storage/db.py` — репозитории поверх нативного Kristall journal;
* `config.py` — конфигурация;
* `observability/system_log.py` — системный лог (запуск и сбои до появления Trace);
* `file_routes.py` — маршруты работы с файлами;
* `mcp_routes.py` — маршруты MCP.

### 3. MCP‑инструменты
* `tool_providers/filesystem.py` — работа с файловой системой;
* `tool_providers/git.py` — Git‑операции;
* `tool_providers/profiler.py` — профайлинг;
* `archiver.py` — архивация;
* `file_manager.py` — управление файлами.

### 4. AI‑адаптеры и агенты
* `cli_agent.py` — CLI/Termux‑адаптер к каноническому backend runtime;
* `issue_runner.py` — GitHub issue adapter;
* `local_tool_agent.py` — Android/local tool adapter;
* Yandex AI Studio подключается через общий provider/Responses pipeline, а не напрямую из интерфейсных адаптеров.

### 5. Система хранения
* **Нативный Kristall journal** (`storage/db.py`) — основное хранилище данных; SQLite и PostgreSQL выведены из эксплуатации;
* файловая система — векторные хранилища и файлы.

## Взаимодействие компонентов

1. **Пользовательский запрос**:
   * браузер загружает статические файлы из `static/`;
   * `chat.js` отправляет запрос на `app.py`.

2. **Обработка на бэкенде**:
   * `app.py` маршрутизирует запрос;
   * при необходимости обращается к Yandex AI Studio;
   * использует MCP‑инструменты для файловых операций.

3. **Хранение данных**:
   * структурированные данные — в нативном Kristall journal (`storage/db.py`);
   * файлы и векторные данные — в файловой системе.

4. **Логирование**:
   * все действия записываются в соответствующие лог‑файлы (`logs/`).

## Технологический стек

* **Бэкенд**: Python, Flask;
* **Хранилище**: нативный Kristall journal (SQLite и PostgreSQL выведены из эксплуатации);
* **Фронтенд**: HTML, JavaScript, CSS (без React/Vite в основной версии);
* **AI**: Yandex AI Studio;
* **MCP**: собственные инструменты на Python;
* **Логирование**: `observability/system_log.py` (JSONL, ротация, `trace_id`/`invocation_id`).
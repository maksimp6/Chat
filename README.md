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
Скопируйте `.env.example` в `.env` и заполните секреты локально:
```bash
cp .env.example .env
```

Минимальная конфигурация:
```env
YANDEX_API_KEY=<your-yandex-api-key>
YANDEX_PROJECT_ID=<your-yandex-project-id>
YANDEX_BASE_URL=https://ai.api.cloud.yandex.net/v1
HOST=0.0.0.0
PORT=8080
SECRET_KEY=<generate-a-random-secret>
ALICE_OWNER_ID=<stable-owner-id>
```

Для необязательного зеркала execution traces в Supabase добавьте на backend:
```env
SUPABASE_URL=https://<your-project-ref>.supabase.co
SUPABASE_SERVICE_ROLE_KEY=<your-supabase-service-role-key>
```

`SUPABASE_SERVICE_ROLE_KEY` предназначен только для backend. Не передавайте его во frontend и не коммитьте реальное значение.

**Никогда не коммитьте `.env`, API keys или другие credentials.**

### 4. Запуск приложения
```bash
python app.py
```

### 5. Открытие в браузере
Перейдите по адресу: http://localhost:8080

## 🔐 Безопасность конфигурации

Секреты читаются из переменных окружения. В репозитории разрешены только placeholders из `.env.example`.

- `YANDEX_API_KEY` — API-ключ Yandex Cloud.
- `YANDEX_PROJECT_ID` — ID проекта Yandex Cloud.
- `YANDEX_BASE_URL` — базовый URL API.
- `SUPABASE_URL` — URL проекта Supabase для серверного зеркала трейсов.
- `SUPABASE_SERVICE_ROLE_KEY` — backend-only credential для записи в закрытую таблицу трейсов.
- `SECRET_KEY` — секрет Flask-сессий, если используется приложением.

Зеркало Supabase является необязательным и должно быть best-effort: его сбои не должны ломать основной чатовый поток.

Если credential когда-либо попал в Git, считайте его скомпрометированным: отзовите/ротируйте его в соответствующем сервисе. Удаление строки из текущего файла не удаляет её из Git history.

CI выполняет автоматическую проверку репозитория на секреты.

## 📁 Структура проекта

```text
alice_pro/
├── app.py
├── config.py
├── yandex_client.py
├── supabase_trace_mirror.py
├── supabase/
│   └── migrations/
├── templates/
├── static/
├── tests/
├── docs/
├── .env.example
├── .gitignore
└── README.md
```

## 📄 Лицензия

MIT

---
*README актуализирован: 2026-09-16*
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
```

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
- `SECRET_KEY` — секрет Flask-сессий, если используется приложением.

Если credential когда-либо попал в Git, считайте его скомпрометированным: отзовите/ротируйте его в соответствующем сервисе. Удаление строки из текущего файла не удаляет её из Git history.

CI выполняет автоматическую проверку репозитория на секреты.

## 📁 Структура проекта

```text
alice_pro/
├── app.py
├── config.py
├── yandex_client.py
├── templates/
├── static/
├── docs/
├── .env.example
├── .gitignore
└── README.md
```

## 📄 Лицензия

MIT

---
*README актуализирован: 2026-09-16*

# Запуск в режиме разработки

## Подготовка

Выполните [инструкцию установки](installation.md), затем из корня checkout
с активированным виртуальным окружением установите инструменты разработки:

```bash
python -m pip install -r requirements-dev.txt
```

Node.js нужен для запуска JavaScript-тестов (`node tests/*.js`). Android-приложение
и сборка APK удалены; JDK и Android SDK для разработки Logos не требуются.

## Backend

```bash
HOST=127.0.0.1 PORT=8080 python app.py
```

Откройте `http://127.0.0.1:8080`. Переменные, переданные в команде,
имеют приоритет над `.env`. Без `PORT` backend использует `5000`;
шаблон `.env.example` задаёт `8080`.

`app.py` запускается с `use_reloader=False`, поэтому после изменения Python-кода
остановите процесс через Ctrl+C и запустите его заново.
Для проверки доступности используйте `GET /healthz`;
для проверки модели — отдельный запрос в чат после настройки провайдера.

`start.sh` остаётся вспомогательным запуском, но не устанавливает все зависимости
из `requirements.txt`.

## CLI / Termux

`cli_agent.py` — клиент уже запущенного backend. В отдельной сессии из корня
checkout с установленными зависимостями:

```bash
ALICE_BASE_URL=http://127.0.0.1:8080 python cli_agent.py
```

CLI по умолчанию обращается к `http://127.0.0.1:5000`; для приведённого выше
запуска нужно явное `ALICE_BASE_URL`.
Он создаёт диалог через `/api/conversations`, отправляет сообщения в
`/api/chat` и использует стандартный endpoint подтверждения инструментов.
Собственного model/tool loop и локального shell-исполнения в CLI больше нет.
Ключ модели настраивается на backend через «Провайдеры».

Для Termux нет обязательного пути `/sdcard/repo/printer_service`: используйте свой
checkout и доступное Python-окружение. Путь workspace файловых инструментов —
отдельная настройка, он не определяется каталогом запуска CLI.

## Навигация и диагностика

- [Структура репозитория](../architecture/repository-layout.md) — актуальные
  пакеты и остающиеся модули в корне.
- [Канонический AI pipeline](../architecture/ai-execution-pipeline.md) —
  invocation, инструменты, trace и billing.
- [Логирование](../backend/logging.md) и
  [Execution Trace](../architecture/execution_trace.md) — диагностика запросов.
- Скрипты независимого управления телефоном находятся в `scripts/rdc/`.

Правила изменений описаны в [CONTRIBUTING.md](../../CONTRIBUTING.md).

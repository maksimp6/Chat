# Logos Board: локальная альтернатива GitHub Discussions/Issues на Memory DB

**Статус:** изолированный MVP, не production/cutover. **Хранилище:** FileMemoryDB, без SQL.

## Возможности сейчас

- Создание обсуждений, комментариев, решений и «Доски позора».
- Задачи, зависимости, статусы, история и доказательства (по умолчанию **unverified**).
- Явная пользовательская приёмка задачи перед `done`; **никакого merge**.
- Один append-only JSONL Memory DB на локальном диске, `fsync` до подтверждения,
  crash recovery и экспорт полного журнала через `GET /api/export`.
- Idempotency key для каждого POST: повтор при потере HTTP-ответа не создаёт дубль.
- Приватный интерфейс с токеном только в оперативной памяти браузерной вкладки.
- Один writer-процесс; второй `BoardStore` не получает lock.
- HTTP только на `127.0.0.1`, без автоматического публичного доступа.

Не реализованы: публичная авторизация пользователей, роли агентов, Git hosting/PR,
деплой и TLS, production backup encryption, импорт существующих GitHub Discussions,
взаимодействие с GitHub API. GitHub до отдельного разрешения остаётся источником
истины для кодовых PR, а этот сервис служит независимой доской.

## Архитектура

```text
Browser (mobile) -> Logos Board HTTP loopback -> BoardStore
                                               -> FileMemoryDB append+fsync
                                               -> /data/project-board/board.memory
```

- Код сервиса в `project_board/`, UI в `project_board/static/`.
- База `agent_memory.file_memory_db.FileMemoryDB` уже находится в `master`.
- `memory_db.py` **не использовать** для долговечных данных: он process-local/test-only.
- Блокировка `board.memory.board.lock` управляется Logos Board, чтобы не запускать
  два writer-процесса до полной готовности нового Memory DB lock protocol.
- Если ошибка записи оставила неопределённое состояние, экземпляр
  запрещает новые записи до повторного открытия и восстановления.
- Один логический append = одна запись в Memory DB. Проекции строятся из
  проверенного журнала. Чужой код не должен редактировать журнал руками.

## Запуск только в изолированной среде

```bash
export PROJECT_BOARD_DB=/data/project-board/board.memory
export PROJECT_BOARD_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(36))')"
python3 -m project_board.server
```

Адрес: `http://127.0.0.1:8787/`. Токен вводится в форме один раз на вкладку,
в localStorage/cookie он не записывается. Публично открывать порт 8787 **нельзя**.
Для удалённого доступа нужен отдельно проверенный HTTPS reverse proxy с аутентификацией.

Запись текущего подтверждённого инцидента в новую доску, при **остановленном сервере**:

```bash
python3 -m project_board.seed --db /data/project-board/board.memory
```

Повторная команда идемпотентна. Это локальная запись Logos Board,
**не GitHub Discussion**. В демо используются только тестовые данные.

## HTTP API

Все `/api/` методы требуют `Authorization: Bearer <PROJECT_BOARD_TOKEN>`.
Без токена возвращается 401. Health `/healthz` анонимный, без пользовательских данных.

| Метод | Маршрут | Назначение |
| --- | --- | --- |
| GET/POST | `/api/threads` | Список и создание обсуждения |
| GET | `/api/threads/{id}` | Обсуждение и комментарии |
| POST | `/api/threads/{id}/posts` | Опубликовать комментарий |
| GET/POST | `/api/tasks` | Задачи и зависимости |
| GET | `/api/tasks/{id}` | Детали и доказательства |
| POST | `/api/tasks/{id}/status` | Guarded status transition |
| POST | `/api/tasks/{id}/evidence` | Добавить unverified evidence |
| GET | `/api/export` | Полный журнал JSON для переноса |

Каждый POST требует `request_id`, который уникален в пределах одной базы.
При повторе того же request_id c тем же payload сервер возвращает тот же результат;
с другим содержимым возвращает 409.

Доступные статусы: `backlog → ready → in_progress → review → done`,
с веткой `blocked`; зависимости должны быть `done` перед переходом в `ready`.
Только `accepted_by_owner=true` позволяет `review → done`. Это **НЕ merge**.

## Тесты и ограничения приёмки

```bash
python3 -m pytest -q tests/test_project_board.py
python3 -m ruff check project_board tests/test_project_board.py
python3 -m ruff format --check project_board tests/test_project_board.py
node --check project_board/static/app.js
```

Есть реальный HTTP loopback E2E, process lock, parallel writes, restart/replay,
SIGKILL после ACK, torn tail, fail-closed write-fault и idempotency.
Проверка HTTP/JS не заменяет физический browser E2E на Android.

## Дальше (отдельные задачи)

1. Owner authentication/proxy, журнал идентичностей агентов, ACL и audit trace.
2. Проверенный read-only импорт GitHub Discussions/Issues с ID mapping,
   idempotency и full-count reconciliation, без потери данных.
3. Шифрование резервных копий, restore drill, объективные бенчмарки и fault testing.
4. Публичный HTTPS deploy и независимый Android E2E только с разрешения владельца.
5. Самостоятельное Git-хранилище, если нужен полный отказ от GitHub.

До пользовательской приёмки нет ни cutover, ни выключения существующих сервисов.

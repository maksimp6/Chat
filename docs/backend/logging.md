# Логирование и диагностика

## Две ступени

1. **Execution Trace** (`observability/trace_manager.py`) — основа диагностики
   выполнения: события агента, запросы/ответы API, инструменты, ошибки и время.
   Каждая запись привязана к `trace_id` / `invocation_id`.
2. **Системный лог** (`observability/system_log.py`) — небольшой централизованный
   лог процесса для запуска сервера и сбоев до появления Trace. Это не второй Trace.

## Системный лог

* Файл: `logs/system.jsonl` (каталог задаётся `LOGOS_LOG_DIR`), одна JSON-запись на строку;
* Ротация: 5 MB, 3 резервные копии; дублируется в stderr;
* Уровень: `LOGOS_LOG_LEVEL` (по умолчанию `INFO`); реальные уровни сохраняются;
* Поля: `timestamp`, `level`, `logger`, `message`, `trace_id`, `invocation_id`, `exception`;
* Если в контексте привязан Trace, записи WARNING и выше дополнительно попадают в него
  событием `system_log`.

Использование:

```python
from observability.system_log import get_logger

log = get_logger("component")
```

## Поиск ошибок

1. Сбой при запуске или до Trace — `logs/system.jsonl`.
2. Сбой в запросе — Trace по `trace_id`/`invocation_id` (ошибки, события, тайминги).
3. Связать записи лога и Trace можно по `trace_id`.

"""Модуль профайлинга, инспекции и глубокого анализа приложения Logos."""

import cProfile
import pstats
import io
import sys
import gc
import logging

from kristall import KristallError
from storage.db import memory_file_path

logger = logging.getLogger("logos_profiler")


def profile_application_performance(arguments: dict, cfg: dict = None) -> dict:
    """Запускает экспресс-профайлинг ключевых компонентов системы."""
    sort_by = arguments.get("sort", "cumulative")
    lines_count = int(arguments.get("limit", 15))

    pr = cProfile.Profile()
    pr.enable()

    # Имитация сбора метрик и инспекции памяти
    gc.collect()
    total_objects = len(gc.get_objects())

    pr.disable()
    s = io.StringIO()
    ps = pstats.Stats(pr, stream=s).sort_stats(sort_by)
    ps.print_stats(lines_count)

    return {
        "success": True,
        "memory_objects_count": total_objects,
        "profiling_report": s.getvalue(),
    }


def _native_journal_status() -> dict[str, object]:
    """Describe the native Kristall journal without opening, locking or repairing it."""
    try:
        path = memory_file_path()
    except KristallError as exc:
        return {"path": None, "exists": False, "size_bytes": 0, "error": str(exc)}
    exists = path.exists()
    return {
        "path": str(path),
        "exists": exists,
        "size_bytes": path.stat().st_size if exists else 0,
    }


def inspect_app_internals(arguments: dict, cfg: dict = None) -> dict:
    """Возвращает полную карту модулей, путей и состояние окружения приложения."""
    loaded_modules = [m for m in sys.modules.keys() if not m.startswith("_")]

    return {
        "success": True,
        "python_version": sys.version,
        "platform": sys.platform,
        "database": _native_journal_status(),
        "active_modules_count": len(loaded_modules),
        "sample_modules": loaded_modules[:20],
    }


TOOL_REGISTRY = {
    "profile_application_performance": {
        "func": profile_application_performance,
        "description": "Выполнить профайлинг производительности кода и инспекцию памяти приложения.",
        "parameters": {
            "type": "object",
            "properties": {
                "sort": {
                    "type": "string",
                    "description": "Критерий сортировки (cumulative, time, calls)",
                },
                "limit": {"type": "integer", "description": "Количество строк отчета"},
            },
            "required": [],
        },
    },
    "inspect_app_internals": {
        "func": inspect_app_internals,
        "description": "Получить полную карту внутренних компонентов, модулей и состояния базы данных Logos.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

PROFILER_TOOLS = TOOL_REGISTRY

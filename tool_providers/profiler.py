"""Модуль профайлинга, инспекции и глубокого анализа приложения Logos."""

import cProfile
import gc
import io
import logging
import pstats
import sys

from tool_providers.host import bound, memory_file_path

logger = logging.getLogger("logos.tool_providers.profiler")

SORT_KEYS = ("cumulative", "time", "calls")


def profile_application_performance(arguments: dict, cfg: dict = None) -> dict:
    """Запускает экспресс-профайлинг ключевых компонентов системы."""
    sort_by = arguments.get("sort") or "cumulative"
    if sort_by not in SORT_KEYS:
        raise ValueError(f"sort must be one of {', '.join(SORT_KEYS)}")
    lines_count = int(15 if arguments.get("limit") is None else arguments["limit"])

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
    except bound.KristallError as exc:
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


# Read-only diagnostics of this process: no approval boundary is crossed.
_READ_ONLY = {"read_only": True, "requires_approval": False, "risk_level": "low"}

TOOL_REGISTRY = {
    "profile_application_performance": {
        "func": profile_application_performance,
        "description": "Выполнить профайлинг производительности кода и инспекцию памяти приложения.",
        "parameters": {
            "type": "object",
            "properties": {
                "sort": {
                    "type": "string",
                    "enum": list(SORT_KEYS),
                    "description": "Критерий сортировки (cumulative, time, calls)",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 200,
                    "description": "Количество строк отчета",
                },
            },
            "required": [],
        },
        **_READ_ONLY,
    },
    "inspect_app_internals": {
        "func": inspect_app_internals,
        "description": "Получить полную карту внутренних компонентов, модулей и состояния базы данных Logos.",
        "parameters": {"type": "object", "properties": {}, "required": []},
        **_READ_ONLY,
    },
}

PROFILER_TOOLS = TOOL_REGISTRY

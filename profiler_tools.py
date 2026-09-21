"""Модуль профайлинга, инспекции и глубокого анализа приложения Alice Pro."""
import cProfile
import pstats
import io
import os
import sys
import gc
import logging

logger = logging.getLogger("alice_profiler")

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
        "profiling_report": s.getvalue()
    }

def inspect_app_internals(arguments: dict, cfg: dict = None) -> dict:
    """Возвращает полную карту модулей, путей и состояние окружения приложения."""
    loaded_modules = [m for m in sys.modules.keys() if not m.startswith('_')]
    database_url = os.getenv("ALICE_DATABASE_URL") or os.getenv("DATABASE_URL")
    db_path = os.path.abspath(os.getenv("ALICE_DB_PATH", "alice_pro.db"))
    db_backend = "postgresql" if database_url else "sqlite"
    db_exists = bool(database_url) or os.path.exists(db_path)
    db_size = os.path.getsize(db_path) if not database_url and os.path.exists(db_path) else 0
    
    return {
        "success": True,
        "python_version": sys.version,
        "platform": sys.platform,
        "database": {
            "path": db_path,
            "exists": db_exists,
            "size_bytes": db_size
        },
        "active_modules_count": len(loaded_modules),
        "sample_modules": loaded_modules[:20]
    }

TOOL_REGISTRY = {
    "profile_application_performance": {
        "func": profile_application_performance,
        "description": "Выполнить профайлинг производительности кода и инспекцию памяти приложения.",
        "parameters": {
            "type": "object",
            "properties": {
                "sort": {"type": "string", "description": "Критерий сортировки (cumulative, time, calls)"},
                "limit": {"type": "integer", "description": "Количество строк отчета"}
            },
            "required": []
        }
    },
    "inspect_app_internals": {
        "func": inspect_app_internals,
        "description": "Получить полную карту внутренних компонентов, модулей и состояния базы данных Alice Pro.",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": []
        }
    }
}

PROFILER_TOOLS = TOOL_REGISTRY

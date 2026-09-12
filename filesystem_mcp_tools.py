"""Локальные MCP-инструменты для работы с файловой системой с защитой от Path Traversal."""
import os
import logging

logger = logging.getLogger("filesystem_mcp")

BASE_DIR = os.path.realpath("/storage/emulated/0/alice_pro")

def _get_abs_path(path: str) -> str:
    if not path or path == ".":
        return BASE_DIR
    full_path = os.path.realpath(os.path.join(BASE_DIR, path))
    if not full_path.startswith(BASE_DIR + os.sep) and full_path != BASE_DIR:
        raise ValueError(f"Доступ запрещен: попытка выхода за пределы директории проекта ({path})")
    return full_path

def read_file(args: dict, cfg: dict = None) -> dict:
    try:
        path = args.get("path", "")
        if not path:
            return {"success": False, "error": "Параметр 'path' обязателен"}
        safe_path = _get_abs_path(path)
        if not os.path.isfile(safe_path):
            return {"success": False, "error": f"Файл не найден: {safe_path}"}
        with open(safe_path, 'r', encoding='utf-8') as f:
            content = f.read()
        return {"success": True, "content": content, "path": safe_path}
    except ValueError as ve:
        return {"success": False, "error": str(ve)}
    except Exception as e:
        logger.exception(f"Ошибка чтения файла {path}: {e}")
        return {"success": False, "error": f"Внутренняя ошибка: {str(e)}"}

def write_file(args: dict, cfg: dict = None) -> dict:
    try:
        path = args.get("path", "")
        content = args.get("content", "")
        if not path:
            return {"success": False, "error": "Параметр 'path' обязателен"}
        safe_path = _get_abs_path(path)
        os.makedirs(os.path.dirname(safe_path), exist_ok=True)
        with open(safe_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return {"success": True, "message": f"Файл успешно записан: {safe_path}", "path": safe_path}
    except ValueError as ve:
        return {"success": False, "error": str(ve)}
    except Exception as e:
        logger.exception(f"Ошибка записи файла {path}: {e}")
        return {"success": False, "error": f"Внутренняя ошибка: {str(e)}"}

def list_directory(args: dict, cfg: dict = None) -> dict:
    try:
        path = args.get("path", ".")
        safe_path = _get_abs_path(path)
        if not os.path.isdir(safe_path):
            return {"success": False, "error": f"Директория не найдена: {safe_path}"}
        items = []
        for item in os.listdir(safe_path):
            item_path = os.path.join(safe_path, item)
            items.append({
                "name": item,
                "is_dir": os.path.isdir(item_path),
                "size": os.path.getsize(item_path) if os.path.isfile(item_path) else 0
            })
        return {"success": True, "items": items, "path": safe_path}
    except ValueError as ve:
        return {"success": False, "error": str(ve)}
    except Exception as e:
        logger.exception(f"Ошибка чтения директории {path}: {e}")
        return {"success": False, "error": f"Внутренняя ошибка: {str(e)}"}

FILESYSTEM_TOOLS = {
    "read_file": {
        "func": read_file,
        "description": "Чтение содержимого файла из разрешенной директории проекта.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Относительный или абсолютный путь к файлу"}
            },
            "required": ["path"]
        },
        "requires_approval": True
    },
    "write_file": {
        "func": write_file,
        "description": "Запись содержимого в файл в разрешенной директории проекта.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Путь к файлу для записи"},
                "content": {"type": "string", "description": "Содержимое для записи"}
            },
            "required": ["path", "content"]
        },
        "requires_approval": True
    },
    "list_directory": {
        "func": list_directory,
        "description": "Получение списка файлов и директорий в указанном пути.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Путь к директории (по умолчанию '.')"}
            },
            "required": []
        },
        "requires_approval": False
    }
}

def execute_fs_tool(tool_name: str, arguments: dict, cfg: dict = None) -> dict:
    if tool_name not in FILESYSTEM_TOOLS:
        return {"error": f"Неизвестный инструмент: {tool_name}"}
    tool = FILESYSTEM_TOOLS[tool_name]
    try:
        return tool["func"](arguments, cfg or {})
    except Exception as e:
        logger.exception(f"Ошибка выполнения инструмента {tool_name}: {e}")
        return {"error": f"Внутренняя ошибка: {str(e)}"}

import os
"""Local Termux:API integration tools for Android OS."""
import subprocess
import json
import logging

logger = logging.getLogger("termux_mcp")

def _run_termux_cmd(cmd: list, timeout: int = 10) -> dict:
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        out = res.stdout.strip()
        err = res.stderr.strip()
        if res.returncode == 0:
            try:
                return {"success": True, "data": json.loads(out)}
            except Exception:
                return {"success": True, "output": out or "OK"}
        return {"success": False, "error": err or f"Process exited with code {res.returncode}"}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": f"Timeout executing {cmd[0]}"}
    except Exception as e:
        logger.exception(f"Error executing {cmd[0]}: {e}")
        return {"success": False, "error": str(e)}

def termux_toast(args: dict, cfg: dict) -> dict:
    msg = args.get("message", "")
    return _run_termux_cmd(["termux-toast", "-s", msg])

def termux_vibrate(args: dict, cfg: dict) -> dict:
    ms = str(args.get("duration_ms", 500))
    return _run_termux_cmd(["termux-vibrate", "-d", ms])

def termux_battery_status(args: dict, cfg: dict) -> dict:
    return _run_termux_cmd(["termux-battery-status"])

def termux_torch(args: dict, cfg: dict) -> dict:
    state = "on" if args.get("enabled", True) else "off"
    return _run_termux_cmd(["termux-torch", state])

def termux_clipboard_get(args: dict, cfg: dict) -> dict:
    return _run_termux_cmd(["termux-clipboard-get"])

def termux_clipboard_set(args: dict, cfg: dict) -> dict:
    text = args.get("text", "")
    return _run_termux_cmd(["termux-clipboard-set", text])

def termux_tts_speak(args: dict, cfg: dict) -> dict:
    text = args.get("text", "")
    return _run_termux_cmd(["termux-tts-speak", text])

def termux_notification(args: dict, cfg: dict) -> dict:
    title = args.get("title", "Alice Pro")
    content = args.get("content", "")
    return _run_termux_cmd(["termux-notification", "--title", title, "--content", content])

def termux_camera_photo(args: dict, cfg: dict) -> dict:
    import time
    camera_id = str(args.get("camera_id", 0))
    file_name = args.get("file_name") or f"photo_{int(time.time())}.jpg"
    file_path = os.path.abspath(file_name)
    res = _run_termux_cmd(["termux-camera-photo", "-c", camera_id, file_path], timeout=25)
    if res.get("success"):
        return {
            "success": True,
            "message": f"Фото успешно сохранено: {file_path}",
            "file_path": file_path,
            "camera_id": camera_id
        }
    return res

def termux_open_app_settings(args: dict, cfg: dict) -> dict:
    screen = args.get("screen", "overlay")
    pkg = args.get("package_name", "com.termux.api")

    if screen == "overlay":
        cmd = ["am", "start", "-a", "android.settings.action.MANAGE_OVERLAY_PERMISSION", "-d", f"package:{pkg}"]
    elif screen == "battery":
        cmd = ["am", "start", "-a", "android.settings.IGNORE_BATTERY_OPTIMIZATION_SETTINGS"]
    else:  # "app_details" — общая карточка приложения и список разрешений
        cmd = ["am", "start", "-a", "android.settings.APPLICATION_DETAILS_SETTINGS", "-d", f"package:{pkg}"]

    res = _run_termux_cmd(cmd, timeout=10)
    if res.get("success"):
        return {
            "success": True,
            "message": f"Системный экран настроек ({screen}) для {pkg} успешно открыт на экране телефона."
        }
    return res

def remove_file(args: dict, cfg: dict) -> dict:
    import glob
    pattern = args.get("pattern") or args.get("path")
    if not pattern:
        return {"success": False, "error": "Не указан путь или паттерн для удаления"}
    matched = glob.glob(pattern)
    if not matched:
        return {"success": True, "message": "Файлы по данному шаблону не найдены"}
    deleted = []
    for f in matched:
        try:
            if os.path.isfile(f):
                os.remove(f)
                deleted.append(f)
        except Exception as e:
            return {"success": False, "error": f"Ошибка при удалении {f}: {str(e)}"}
    return {"success": True, "message": f"Удалено файлов: {len(deleted)} ({', '.join(deleted)})"}

TERMUX_TOOLS = {
    "remove_file": {
        "func": remove_file,
        "description": "Удалить один или несколько файлов по имени или шаблону (например, 'photo_*.jpg').",
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Имя файла или шаблон поиска (например, photo_*.jpg)"
                }
            },
            "required": ["pattern"]
        }
    },

    "termux_open_app_settings": {
        "func": termux_open_app_settings,
        "description": "Открыть экран системных настроек Android (разрешение 'Поверх других приложений', карточка разрешений Termux:API или настройки батареи).",
        "parameters": {
            "type": "object",
            "properties": {
                "screen": {
                    "type": "string",
                    "enum": ["overlay", "app_details", "battery"],
                    "description": "Тип экрана: 'overlay' — доступ поверх других окон (для съемки в фоне), 'app_details' — все разрешения приложения, 'battery' — работа в фоне/аккумулятор."
                },
                "package_name": {
                    "type": "string",
                    "description": "Имя пакета (по умолчанию 'com.termux.api')"
                }
            },
            "required": []
        }
    },

    "termux_camera_photo": {
        "func": termux_camera_photo,
        "description": "Сделать снимок с камеры смартфона (0 — задняя/основная, 1 — фронтальная) и сохранить его в локальный файл.",
        "parameters": {
            "type": "object",
            "properties": {
                "camera_id": {
                    "type": "integer",
                    "description": "ID камеры: 0 для задней камеры, 1 для селфи-камеры (по умолчанию 0)"
                },
                "file_name": {
                    "type": "string",
                    "description": "Имя файла для сохранения снимка (по умолчанию генерируется автоматически, например photo_1789211000.jpg)"
                }
            },
            "required": []
        }
    },

    "termux_battery_status": {
        "func": termux_battery_status,
        "description": "Получить детальную информацию о батарее устройства (процент заряда, температура, статус зарядки).",
        "parameters": {"type": "object", "properties": {}, "required": []}
    },
    "termux_toast": {
        "func": termux_toast,
        "description": "Показать системное всплывающее сообщение (Toast) на экране смартфона.",
        "parameters": {
            "type": "object",
            "properties": {"message": {"type": "string", "description": "Текст всплывающего уведомления"}},
            "required": ["message"]
        }
    },
    "termux_vibrate": {
        "func": termux_vibrate,
        "description": "Включить вибрацию телефона на заданное количество миллисекунд.",
        "parameters": {
            "type": "object",
            "properties": {"duration_ms": {"type": "integer", "description": "Длительность вибрации в миллисекундах (по умолчанию 500)"}},
            "required": []
        }
    },
    "termux_torch": {
        "func": termux_torch,
        "description": "Управление фонариком/вспышкой телефона (включение/выключение).",
        "parameters": {
            "type": "object",
            "properties": {"enabled": {"type": "boolean", "description": "true — включить фонарик, false — выключить"}},
            "required": ["enabled"]
        }
    },
    "termux_clipboard_get": {
        "func": termux_clipboard_get,
        "description": "Прочитать текущий текст из буфера обмена Android.",
        "parameters": {"type": "object", "properties": {}, "required": []}
    },
    "termux_clipboard_set": {
        "func": termux_clipboard_set,
        "description": "Скопировать текст в буфер обмена Android.",
        "parameters": {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "Текст для помещения в буфер"}},
            "required": ["text"]
        }
    },
    "termux_tts_speak": {
        "func": termux_tts_speak,
        "description": "Озвучить текст голосом через системный движок Android TTS.",
        "parameters": {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "Текст для воспроизведения"}},
            "required": ["text"]
        }
    },
    "termux_notification": {
        "func": termux_notification,
        "description": "Создать уведомление в шторке Android.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Заголовок уведомления"},
                "content": {"type": "string", "description": "Основной текст уведомления"}
            },
            "required": ["content"]
        }
    }
}

def execute_termux_tool(tool_name: str, arguments: dict, cfg: dict = None) -> dict:
    if tool_name not in TERMUX_TOOLS:
        return {"error": f"Неизвестный Termux инструмент: {tool_name}"}
    try:
        return TERMUX_TOOLS[tool_name]["func"](arguments, cfg or {})
    except Exception as e:
        logger.exception(f"Ошибка вызова инструмента {tool_name}: {e}")
        return {"error": f"Внутренняя ошибка вызова: {str(e)}"}

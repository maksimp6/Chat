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

TERMUX_TOOLS = {
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

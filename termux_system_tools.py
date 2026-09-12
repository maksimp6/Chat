"""Модуль системных инструментов Termux с гранулярным контролем доступа."""
import subprocess
import json
import logging

logger = logging.getLogger("termux_system")

def _run_termux(cmd: list, timeout: int = 10) -> dict:
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        out = res.stdout.strip()
        err = res.stderr.strip()
        if res.returncode == 0:
            try:
                return {"success": True, "data": json.loads(out)}
            except Exception:
                return {"success": True, "output": out or "OK"}
        return {"success": False, "error": err or f"Код ошибки: {res.returncode}"}
    except Exception as e:
        logger.exception(f"Ошибка вызова {cmd}: {e}")
        return {"success": False, "error": str(e)}

# === Функции чтения (Безопасные: requires_approval = False) ===

def get_battery(args: dict, cfg: dict) -> dict:
    return _run_termux(["termux-battery-status"])

def get_volume(args: dict, cfg: dict) -> dict:
    return _run_termux(["termux-volume"])

def get_wifi_info(args: dict, cfg: dict) -> dict:
    return _run_termux(["termux-wifi-connectioninfo"])

# === Функции изменения параметров (Требуют подтверждения: requires_approval = True) ===

def set_volume(args: dict, cfg: dict) -> dict:
    stream = args.get("stream", "music")
    vol = args.get("volume", 5)
    return _run_termux(["termux-volume", stream, str(vol)])

def set_brightness(args: dict, cfg: dict) -> dict:
    level = args.get("level", 128)
    return _run_termux(["termux-brightness", str(level)])

def set_torch(args: dict, cfg: dict) -> dict:
    state = "on" if args.get("enabled", False) else "off"
    return _run_termux(["termux-torch", state])

def set_vibration(args: dict, cfg: dict) -> dict:
    duration = args.get("duration_ms", 300)
    return _run_termux(["termux-vibrate", "-d", str(duration)])


# === Реестр системных инструментов с метаданными прав ===

SYSTEM_TOOLS = {
    "get_system_battery": {
        "func": get_battery,
        "requires_approval": False,
        "description": "Получить статус батареи: уровень заряда, температура, состояние зарядки.",
        "parameters": {"type": "object", "properties": {"dummy": {"type": "string", "description": "unused"}}, "required": []}
    },
    "get_system_volume": {
        "func": get_volume,
        "requires_approval": False,
        "description": "Получить текущие уровни громкости всех аудиоканалов устройства (music, call, alarm и др.).",
        "parameters": {"type": "object", "properties": {"dummy": {"type": "string", "description": "unused"}}, "required": []}
    },
    "get_wifi_status": {
        "func": get_wifi_info,
        "requires_approval": False,
        "description": "Получить данные о текущем Wi-Fi соединении (SSID, IP, скорость, сила сигнала).",
        "parameters": {"type": "object", "properties": {"dummy": {"type": "string", "description": "unused"}}, "required": []}
    },
    "set_system_volume": {
        "func": set_volume,
        "requires_approval": True,
        "description": "Установить уровень громкости определенного аудиоканала устройства.",
        "parameters": {
            "type": "object",
            "properties": {
                "stream": {
                    "type": "string",
                    "description": "Канал громкости"
                },
                "volume": {
                    "type": "integer",
                    "description": "Уровень громкости (обычно от 0 до 15)"
                }
            },
            "required": ["stream", "volume"]
        }
    },
    "set_screen_brightness": {
        "func": set_brightness,
        "requires_approval": True,
        "description": "Изменить яркость экрана смартфона (значение от 0 до 255).",
        "parameters": {
            "type": "object",
            "properties": {
                "level": {
                    "type": "integer",
                                        "description": "Уровень яркости (0-255)"
                }
            },
            "required": ["level"]
        }
    },
    "set_torch": {
        "func": set_torch,
        "requires_approval": True,
        "description": "Включить или выключить фонарик телефона.",
        "parameters": {
            "type": "object",
            "properties": {
                "enabled": {"type": "boolean", "description": "true для включения, false для выключения"}
            },
            "required": ["enabled"]
        }
    },
    "trigger_vibration": {
        "func": set_vibration,
        "requires_approval": False,
        "description": "Подать короткий тактильный вибросигнал на устройстве.",
        "parameters": {
            "type": "object",
            "properties": {
                "duration_ms": {"type": "integer", "description": "Длительность вибрации в миллисекундах"}
            },
            "required": []
        }
    }
}

def execute_system_tool(name: str, args: dict, cfg: dict = None) -> dict:
    if name not in SYSTEM_TOOLS:
        return {"error": f"Неизвестный системный инструмент: {name}"}
    return SYSTEM_TOOLS[name]["func"](args, cfg or {})

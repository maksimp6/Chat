"""Расширенные инструменты Termux:API для Alice Pro.

Поддерживает все разрешения Android: батарея, Wi-Fi, локация (GPS/Network),
буфер обмена, уведомления, всплывающие сообщения (Toast), синтез речи (TTS),
фонарик, виброотклик, яркость, громкость, контакты, SMS и камера.
"""
import subprocess
import json
import logging
import shutil
import os

logger = logging.getLogger("termux_mcp")

def _run_termux_cmd(cmd: list, timeout: int = 15) -> dict:
    binary = cmd[0]
    if not shutil.which(binary):
        return {
            "success": False,
            "error": f"Утилита '{binary}' не найдена. Убедитесь, что установлены пакеты 'termux-api' в Termux и приложение Termux:API в Android."
        }
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False
        )
        stdout = proc.stdout.strip()
        stderr = proc.stderr.strip()

        if proc.returncode != 0:
            return {"success": False, "error": stderr or f"Код возврата {proc.returncode}"}

        if not stdout:
            return {"success": True, "output": "(успешно / вывод пуст)"}

        try:
            return {"success": True, "data": json.loads(stdout)}
        except json.JSONDecodeError:
            return {"success": True, "output": stdout}

    except subprocess.TimeoutExpired:
        return {"success": False, "error": f"Превышено время ожидания ({timeout} сек)"}
    except Exception as e:
        logger.exception(f"Ошибка вызова {binary}: {e}")
        return {"success": False, "error": str(e)}

# --- 1. Питание и Сеть ---

def get_battery_status(args: dict, cfg: dict = None) -> dict:
    """Получить статус батареи (уровень заряда, статус подключения, температуру)."""
    return _run_termux_cmd(["termux-battery-status"])

def get_wifi_status(args: dict, cfg: dict = None) -> dict:
    """Получить сведения о текущем Wi-Fi подключении (SSID, BSSID, IP, скорость, сила сигнала)."""
    return _run_termux_cmd(["termux-wifi-connectioninfo"])

# --- 2. Локация и Сенсоры ---

def get_location(args: dict, cfg: dict = None) -> dict:
    """Получить GPS/сетевые координаты устройства (требуется разрешение на геопозицию)."""
    provider = args.get("provider", "network")  # 'gps', 'network', 'passive'
    request_type = args.get("type", "once")     # 'once', 'last'
    cmd = ["termux-location", "-p", str(provider), "-r", str(request_type)]
    return _run_termux_cmd(cmd, timeout=25)

# --- 3. Буфер обмена (Clipboard) ---

def get_clipboard(args: dict, cfg: dict = None) -> dict:
    """Считать текущий текст из системного буфера обмена Android."""
    return _run_termux_cmd(["termux-clipboard-get"])

def set_clipboard(args: dict, cfg: dict = None) -> dict:
    """Скопировать текст в системный буфер обмена Android."""
    text = args.get("text", "")
    if not text:
        return {"error": "Параметр 'text' обязателен"}
    return _run_termux_cmd(["termux-clipboard-set", str(text)])

# --- 4. Уведомления и Обратная связь ---

def send_notification(args: dict, cfg: dict = None) -> dict:
    """Показать всплывающее уведомление в системной шторке Android."""
    title = args.get("title", "Alice Pro")
    content = args.get("content") or args.get("message", "")
    if not content:
        return {"error": "Параметр 'content' обязателен"}
    cmd = ["termux-notification", "--title", str(title), "--content", str(content)]
    if args.get("id"):
        cmd.extend(["--id", str(args["id"])])
    if args.get("priority"):
        cmd.extend(["--priority", str(args["priority"])])  # high, low, max, min, default
    return _run_termux_cmd(cmd)

def show_toast(args: dict, cfg: dict = None) -> dict:
    """Показать быстрое всплывающее сообщение (Android Toast)."""
    message = args.get("message") or args.get("text", "")
    if not message:
        return {"error": "Параметр 'message' обязателен"}
    cmd = ["termux-toast"]
    if args.get("short", True):
        cmd.append("-s")
    cmd.append(str(message))
    return _run_termux_cmd(cmd)

def tts_speak(args: dict, cfg: dict = None) -> dict:
    """Озвучить текст голосом через стандартный движок Android Text-To-Speech."""
    text = args.get("text", "")
    if not text:
        return {"error": "Параметр 'text' обязателен"}
    cmd = ["termux-tts-speak"]
    if args.get("rate"):
        cmd.extend(["-r", str(args["rate"])])
    if args.get("pitch"):
        cmd.extend(["-p", str(args["pitch"])])
    cmd.append(str(text))
    return _run_termux_cmd(cmd, timeout=20)

def trigger_vibration(args: dict, cfg: dict = None) -> dict:
    """Подать вибросигнал заданной длительности (в миллисекундах)."""
    duration = int(args.get("duration_ms", 300))
    return _run_termux_cmd(["termux-vibrate", "-d", str(duration)])

def set_torch(args: dict, cfg: dict = None) -> dict:
    """Включить или выключить фонарик смартфона."""
    enabled = args.get("enabled", True)
    if isinstance(enabled, str):
        enabled = enabled.lower() in ("true", "1", "on")
    state = "on" if enabled else "off"
    return _run_termux_cmd(["termux-torch", state])

# --- 5. Звук и Экран ---

def get_system_volume(args: dict, cfg: dict = None) -> dict:
    """Получить уровни громкости всех аудиоканалов устройства."""
    return _run_termux_cmd(["termux-volume"])

def set_system_volume(args: dict, cfg: dict = None) -> dict:
    """Установить громкость для аудиоканала (music, call, system, ring, alarm, notification)."""
    stream = args.get("stream", "music")
    volume = args.get("volume")
    if volume is None:
        return {"error": "Параметр 'volume' обязателен"}
    return _run_termux_cmd(["termux-volume", str(stream), str(volume)])

def set_screen_brightness(args: dict, cfg: dict = None) -> dict:
    """Установить яркость экрана (от 0 до 255) или включить автояркость ('auto')."""
    level = args.get("level", 128)
    return _run_termux_cmd(["termux-brightness", str(level)])

# --- 6. Контакты, SMS и Камера ---

def list_contacts(args: dict, cfg: dict = None) -> dict:
    """Получить список контактов из телефонной книги устройства."""
    return _run_termux_cmd(["termux-contact-list"], timeout=20)

def list_sms(args: dict, cfg: dict = None) -> dict:
    """Прочитать список входящих/исходящих SMS-сообщений."""
    limit = int(args.get("limit", 10))
    offset = int(args.get("offset", 0))
    cmd = ["termux-sms-list", "-l", str(limit), "-o", str(offset)]
    if args.get("type"):
        cmd.extend(["-t", str(args["type"])])  # all, inbox, sent, draft, outbox
    return _run_termux_cmd(cmd, timeout=20)

def send_sms(args: dict, cfg: dict = None) -> dict:
    """Отправить SMS-сообщение на указанный номер (требует подтверждения)."""
    number = args.get("phone_number") or args.get("number")
    text = args.get("message") or args.get("text")
    if not number or not text:
        return {"error": "Параметры 'phone_number' и 'message' обязательны"}
    return _run_termux_cmd(["termux-sms-send", "-n", str(number), str(text)], timeout=15)

def take_photo(args: dict, cfg: dict = None) -> dict:
    """Сделать фото с камеры смартфона (0 = задняя, 1 = передняя)."""
    camera_id = str(args.get("camera_id", "0"))
    out_file = args.get("output_path") or "/storage/emulated/0/alice_pro/captured_photo.jpg"
    out_dir = os.path.dirname(out_file)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    return _run_termux_cmd(["termux-camera-photo", "-c", camera_id, out_file], timeout=25)

# --- Реестр инструментов для Yandex AI Studio и MCP Routes ---

TOOL_REGISTRY = {
    "get_system_battery": {
        "func": get_battery_status,
        "description": "Получить статус батареи: уровень заряда, температура, состояние зарядки.",
        "parameters": {"type": "object", "properties": {}, "required": []},
        "requires_approval": False
    },
    "get_wifi_status": {
        "func": get_wifi_status,
        "description": "Получить данные о текущем Wi-Fi соединении (SSID, IP, скорость, сила сигнала).",
        "parameters": {"type": "object", "properties": {}, "required": []},
        "requires_approval": False
    },
    "get_device_location": {
        "func": get_location,
        "description": "Получить GPS координаты устройства (широта, долгота, точность, высота).",
        "parameters": {
            "type": "object",
            "properties": {
                "provider": {"type": "string", "enum": ["gps", "network", "passive"], "description": "Источник геоданных (по умолчанию network)"}
            },
            "required": []
        },
        "requires_approval": False
    },
    "get_clipboard": {
        "func": get_clipboard,
        "description": "Прочитать текущий текст из системного буфера обмена Android.",
        "parameters": {"type": "object", "properties": {}, "required": []},
        "requires_approval": False
    },
    "set_clipboard": {
        "func": set_clipboard,
        "description": "Поместить текст в системный буфер обмена Android.",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Текст для копирования"}
            },
            "required": ["text"]
        },
        "requires_approval": False
    },
    "send_notification": {
        "func": send_notification,
        "description": "Отправить всплывающее системное уведомление в шторку Android.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Заголовок уведомления"},
                "content": {"type": "string", "description": "Основной текст уведомления"}
            },
            "required": ["content"]
        },
        "requires_approval": False
    },
    "show_toast": {
        "func": show_toast,
        "description": "Показать короткое всплывающее сообщение (Android Toast) на экране смартфона.",
        "parameters": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Текст всплывающего сообщения"}
            },
            "required": ["message"]
        },
        "requires_approval": False
    },
    "tts_speak": {
        "func": tts_speak,
        "description": "Озвучить текст голосом через системный движок Android TTS.",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Текст для воспроизведения"}
            },
            "required": ["text"]
        },
        "requires_approval": False
    },
    "trigger_vibration": {
        "func": trigger_vibration,
        "description": "Подать короткий тактильный вибросигнал на устройстве.",
        "parameters": {
            "type": "object",
            "properties": {
                "duration_ms": {"type": "integer", "description": "Длительность вибрации в миллисекундах (по умолчанию 300)"}
            },
            "required": []
        },
        "requires_approval": False
    },
    "set_torch": {
        "func": set_torch,
        "description": "Включить или выключить светодиодный фонарик телефона.",
        "parameters": {
            "type": "object",
            "properties": {
                "enabled": {"type": "boolean", "description": "true для включения, false для выключения"}
            },
            "required": ["enabled"]
        },
        "requires_approval": False
    },
    "get_system_volume": {
        "func": get_system_volume,
        "description": "Получить текущие уровни громкости всех аудиоканалов устройства.",
        "parameters": {"type": "object", "properties": {}, "required": []},
        "requires_approval": False
    },
    "set_system_volume": {
        "func": set_system_volume,
        "description": "Установить уровень громкости определенного аудиоканала устройства.",
        "parameters": {
            "type": "object",
            "properties": {
                "stream": {"type": "string", "enum": ["music", "call", "system", "ring", "alarm", "notification"], "description": "Канал аудио"},
                "volume": {"type": "integer", "description": "Уровень громкости (обычно 0-15)"}
            },
            "required": ["stream", "volume"]
        },
        "requires_approval": False
    },
    "set_screen_brightness": {
        "func": set_screen_brightness,
        "description": "Изменить яркость экрана смартфона (значение от 0 до 255).",
        "parameters": {
            "type": "object",
            "properties": {
                "level": {"type": "integer", "description": "Уровень яркости (0-255)"}
            },
            "required": ["level"]
        },
        "requires_approval": False
    },
    "list_contacts": {
        "func": list_contacts,
        "description": "Получить контакты из телефонной книги устройства.",
        "parameters": {"type": "object", "properties": {}, "required": []},
        "requires_approval": False
    },
    "list_sms": {
        "func": list_sms,
        "description": "Прочитать список последних SMS-сообщений.",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Количество SMS (по умолчанию 10)"},
                "offset": {"type": "integer", "description": "Смещение"}
            },
            "required": []
        },
        "requires_approval": False
    },
    "send_sms": {
        "func": send_sms,
        "description": "Отправить SMS-сообщение на номер абонента.",
        "parameters": {
            "type": "object",
            "properties": {
                "phone_number": {"type": "string", "description": "Номер телефона"},
                "message": {"type": "string", "description": "Текст сообщения"}
            },
            "required": ["phone_number", "message"]
        },
        "requires_approval": True
    },
    "take_camera_photo": {
        "func": take_photo,
        "description": "Сделать снимок со встроенной камеры смартфона.",
        "parameters": {
            "type": "object",
            "properties": {
                "camera_id": {"type": "integer", "description": "0 для задней камеры, 1 для фронтальной"},
                "output_path": {"type": "string", "description": "Путь для сохранения файла фото"}
            },
            "required": []
        },
        "requires_approval": False
    }
}

# Алиасы для обратной совместимости
TOOL_REGISTRY["termux_battery_status"] = TOOL_REGISTRY["get_system_battery"]
TOOL_REGISTRY["notification"] = TOOL_REGISTRY["send_notification"]

TERMUX_TOOLS = TOOL_REGISTRY

def execute_termux_tool(tool_name: str, arguments: dict, cfg: dict = None) -> dict:
    if tool_name not in TOOL_REGISTRY:
        return {"error": f"Неизвестный инструмент Termux: {tool_name}"}
    return TOOL_REGISTRY[tool_name]["func"](arguments, cfg or {})

SYSTEM_TOOLS = {}

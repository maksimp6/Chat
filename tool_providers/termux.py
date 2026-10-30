"""Termux tools on the device: Android app settings screens and file removal by pattern.

Toasts, vibration, battery, torch, clipboard, speech, notifications and the camera are
the system category's tools (termux_system.py); they are not repeated here."""

import os

"""Local Termux:API integration tools for Android OS."""
import json
import logging
import shutil
import subprocess

logger = logging.getLogger("logos.tool_providers.termux")


def _run_termux_cmd(cmd: list, timeout: int = 10) -> dict:
    if not shutil.which(cmd[0]):
        return {
            "success": False,
            "error": f"Утилита '{cmd[0]}' не найдена: инструмент работает только на Android "
            "в Termux с пакетом termux-api и приложением Termux:API",
        }
    try:
        res = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
        out = res.stdout.strip()
        err = res.stderr.strip()
        if res.returncode == 0:
            try:
                return {"success": True, "data": json.loads(out)}
            except Exception:
                return {"success": True, "output": out or "OK"}
        return {
            "success": False,
            "error": err or f"Process exited with code {res.returncode}",
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "error": f"Timeout executing {cmd[0]}"}
    except Exception as e:
        logger.exception(f"Error executing {cmd[0]}: {e}")
        return {"success": False, "error": str(e)}


def termux_open_app_settings(args: dict, cfg: dict) -> dict:
    screen = args.get("screen", "overlay")
    pkg = args.get("package_name", "com.termux.api")

    if screen == "overlay":
        cmd = [
            "am",
            "start",
            "-a",
            "android.settings.action.MANAGE_OVERLAY_PERMISSION",
            "-d",
            f"package:{pkg}",
        ]
    elif screen == "battery":
        cmd = [
            "am",
            "start",
            "-a",
            "android.settings.IGNORE_BATTERY_OPTIMIZATION_SETTINGS",
        ]
    else:  # "app_details" — общая карточка приложения и список разрешений
        cmd = [
            "am",
            "start",
            "-a",
            "android.settings.APPLICATION_DETAILS_SETTINGS",
            "-d",
            f"package:{pkg}",
        ]

    res = _run_termux_cmd(cmd, timeout=10)
    if res.get("success"):
        return {
            "success": True,
            "message": f"Системный экран настроек ({screen}) для {pkg} успешно открыт на экране телефона.",
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
    return {
        "success": True,
        "message": f"Удалено файлов: {len(deleted)} ({', '.join(deleted)})",
    }


TERMUX_TOOLS = {
    "remove_file": {
        "func": remove_file,
        "description": "Удалить один или несколько файлов по имени или шаблону (например, 'photo_*.jpg').",
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Имя файла или шаблон поиска (например, photo_*.jpg)",
                }
            },
            "required": ["pattern"],
        },
    },
    "termux_open_app_settings": {
        "func": termux_open_app_settings,
        "description": "Открыть экран системных настроек Android (разрешение 'Поверх других приложений', карточка разрешений Termux:API или настройки батареи).",
        "parameters": {
            "type": "object",
            "properties": {
                "screen": {
                    "type": "string",
                    "description": "Тип экрана: 'overlay' — доступ поверх других окон (для съемки в фоне), 'app_details' — все разрешения приложения, 'battery' — работа в фоне/аккумулятор.",
                },
                "package_name": {
                    "type": "string",
                    "description": "Имя пакета (по умолчанию 'com.termux.api')",
                },
            },
            "required": [],
        },
    },
}


# A tool that needs an Android helper on PATH is not offered on hosts without it
# (Ubuntu runners, servers); remove_file needs nothing.
_REQUIRED_EXECUTABLES = {
    "termux_open_app_settings": "am",
}
for _name, _executable in _REQUIRED_EXECUTABLES.items():
    TERMUX_TOOLS[_name]["metadata"] = {"requires_executables": [_executable]}


def execute_termux_tool(tool_name: str, arguments: dict, cfg: dict = None) -> dict:
    if tool_name not in TERMUX_TOOLS:
        return {"error": f"Неизвестный Termux инструмент: {tool_name}"}
    try:
        return TERMUX_TOOLS[tool_name]["func"](arguments, cfg or {})
    except Exception as e:
        logger.exception(f"Ошибка вызова инструмента {tool_name}: {e}")
        return {"error": f"Внутренняя ошибка вызова: {str(e)}"}

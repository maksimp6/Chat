"""AI-assisted UI theme control with an explicit approval boundary."""

from __future__ import annotations

from typing import Any


THEME_LABELS = {
    "light": "Светлая",
    "dark": "Тёмная",
    "dim": "Приглушённая",
    "high-contrast": "Высокий контраст",
}


def set_ui_theme(arguments: dict[str, Any], cfg: dict | None = None) -> dict[str, Any]:
    """Return a client-side theme action after the existing approval gate allows it."""
    theme = str((arguments or {}).get("theme") or "").strip().lower()
    if theme not in THEME_LABELS:
        return {
            "success": False,
            "error": "Недопустимая цветовая схема",
            "allowed_themes": sorted(THEME_LABELS),
        }

    previous_theme = str((arguments or {}).get("current_theme") or "").strip().lower()
    if previous_theme not in THEME_LABELS:
        previous_theme = None

    return {
        "success": True,
        "theme": theme,
        "label": THEME_LABELS[theme],
        "reason": str((arguments or {}).get("reason") or "").strip(),
        "frontend_action": {
            "type": "set_theme",
            "theme": theme,
            "previous_theme": previous_theme,
        },
    }


THEME_TOOLS = {
    "set_ui_theme": {
        "func": set_ui_theme,
        "description": (
            "Применить цветовую схему Alice Pro. Используй только после явного "
            "согласия пользователя: действие проходит через обязательное подтверждение."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "theme": {
                    "type": "string",
                    "enum": ["light", "dark", "dim", "high-contrast"],
                    "description": "Целевая цветовая схема.",
                },
                "reason": {
                    "type": "string",
                    "description": "Краткое объяснение, почему эта схема подходит пользователю.",
                },
            },
            "required": ["theme"],
        },
        "capabilities": ["theme", "ui"],
        "risk_level": "low",
        "read_only": False,
        "requires_approval": True,
    }
}


__all__ = ["THEME_LABELS", "THEME_TOOLS", "set_ui_theme"]

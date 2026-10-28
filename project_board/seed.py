"""Explicit, idempotent seed of the confirmed missing-controls incident into Logos Board.

This creates data ONLY in the supplied board file. It never calls GitHub or deploys.
"""

from __future__ import annotations
import os

import argparse

from .store import BoardStore

INCIDENT = """## Инцидент · 12 октября 2026 · P2 · FAILED

Ответственный: ChatGPT.

Требование: в конце технического этапа должны быть рабочие кнопки
«▶ Дальше», «✓ ОК», «🔍 Ревью», «✗ Не работает».
«✓ ОК» означает приёмку этапа, НЕ merge.

Факт: после проверки PR #1169 и #1170 ассистент пропустил кнопки.
Пользователь отдельно потребовал «Где кнопки бля». Кнопки появились
лишь в следующем ответе; физическое нажатие на Android не проверено.

Причина на уровне поведения: отсутствовала проверка UI-контракта
перед завершением ответа. Внутренняя программная причина неизвестна.

Регрессия: проверить наличие четырёх интерактивных кнопок,
соответствие действий, доставку команд на Android и отдельно
подтвердить пользовательскую приёмку. Не ставить VERIFIED заранее.
"""


def seed(path: str) -> dict[str, str]:
    """Store one incident once, without external publication."""
    with BoardStore(path) as board:
        shame = next(t for t in board.threads() if t["category"] == "shame")
        post = board.post(
            shame["id"],
            INCIDENT,
            "incident:2026-10-12:chatgpt-missing-controls:v1",
        )
        return {"thread_id": shame["id"], "post_id": post["id"]}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed one confirmed Logos Board incident"
    )
    parser.add_argument("--db", default=os.environ.get("PROJECT_BOARD_DB"))
    arguments = parser.parse_args()
    if not arguments.db:
        parser.error("--db or PROJECT_BOARD_DB is required")
    result = seed(arguments.db)
    print(f"Saved in Memory DB: {result['thread_id']}/{result['post_id']}")


if __name__ == "__main__":
    main()

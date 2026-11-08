"""Production lines: what kind of thing a project makes, with its route card.

Owner, 08.11.2026: "ну например пусть клепает приложения на андроид а другие в вк
сидят". A line is a recipe the founder chooses, the director passes to the project
(``open_project(line=…)``) and production, the inspector and the shelf read:
what the deliverable is, what "ready" means, where it sells. The lines are a
table, not code paths: the same belt runs every line.
"""

from __future__ import annotations

__all__ = ["LINES", "DEFAULT_LINE", "recipe"]

LINES: dict[str, dict[str, str]] = {
    "android": {
        "name": "Приложения для Android",
        "makes": "Готовый исходный проект приложения: Kotlin + Gradle (структура app/, AndroidManifest.xml, "
                 "активности, ресурсы), README с назначением, экранами, сборкой и публикацией, файл "
                 "LICENSE. Один файл не бывает приложением: минимум манифест, build.gradle, MainActivity и "
                 "макеты. Собрать APK на этом компьютере нельзя (нет Android SDK) — продаётся исходный "
                 "проект-шаблон, покупатель собирает сам; это пишется в оффере честно.",
        "ready": "Проект открывается в Android Studio без правок, манифест и Gradle согласованы, README "
                 "объясняет сборку за пять шагов, в коде нет заглушек «TODO».",
        "sells": "Шаблоны и исходники приложений: 1 500–6 000 ₽ за проект; витрина — ВК и Telegram, "
                 "сообщества разработчиков и заказчиков.",
    },
    "bots": {
        "name": "Боты и скрипты",
        "makes": "Рабочий бот или скрипт на Python (стандартная библиотека и requests), с README: что "
                 "делает, как запустить, какие ключи нужны; пример конфигурации.",
        "ready": "Запускается без правок, обрабатывает ошибки, README полный.",
        "sells": "500–3 000 ₽ за бот; ВК и Telegram.",
    },
    "texts": {
        "name": "Тексты",
        "makes": "Готовые тексты: статьи, описания товаров, посты, письма, сценарии — файл .md, "
                 "структура по заказу, без заготовок.",
        "ready": "Факты проверены, объём по заказу, язык чистый.",
        "sells": "200–1 500 ₽ за текст, пакеты — дороже; ВК, Telegram, Kwork.",
    },
    "docs": {
        "name": "Документы и шаблоны",
        "makes": "Наборы шаблонов: договоры, регламенты, инструкции, чек-листы — .md, с пояснением, где и "
                 "что менять. Не юридическая консультация; это пишется в оффере.",
        "ready": "Каждый шаблон полный, поля для заполнения отмечены, пояснение есть.",
        "sells": "500–2 500 ₽ за набор; ВК, Telegram.",
    },
    "data": {
        "name": "Данные и справочники",
        "makes": "Собранные и проверенные наборы: таблицы (.csv), справочники, базы вопросов и ответов, "
                 "с описанием источников и полей.",
        "ready": "Строки проверены, дубликатов нет, описание полей есть.",
        "sells": "500–5 000 ₽ за набор; ВК, Telegram.",
    },
    "translation": {
        "name": "Переводы",
        "makes": "Перевод текста заказчика (русский ⇄ английский), файл .md, с сохранением структуры.",
        "ready": "Смысл и термины точны, формат сохранён.",
        "sells": "300–600 ₽ за тысячу знаков; ВК, Telegram.",
    },
}

DEFAULT_LINE = "texts"


def recipe(line: str | None) -> str:
    """The route card of a line, for a role's prompt."""
    spec = LINES.get(line or "", LINES[DEFAULT_LINE])
    return (
        f"Линия: {spec['name']}.\nЧто делаем: {spec['makes']}\nГотово, когда: {spec['ready']}\n"
        f"Где и за сколько продаётся: {spec['sells']}"
    )


def menu() -> str:
    return "\n".join(f"- {key}: {spec['name']} — {spec['sells']}" for key, spec in LINES.items())

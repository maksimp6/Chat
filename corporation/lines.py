"""Production lines: what kind of thing a project makes, with its route card.

Owner, 09.11.2026: "build me a mobile app company". The company makes mobile
applications, and ``COMPANY_LINES`` are its lines — what the founder picks from and
what the director sees in the menu: source projects for Android and installable mobile
web applications (PWA). The PWA line is the one kind of mobile app this box can finish
completely: no SDK is needed, and the buyer installs it from a link at once.

Owner, 08.11.2026: "ну например пусть клепает приложения на андроид а другие в вк
сидят". A line is a recipe the founder chooses, the director passes to the project
(``open_project(line=…)``) and production, the inspector and the shelf read:
what the deliverable is, what "ready" means, where it sells. The lines are a
table, not code paths: the same belt runs every line. The other recipes stay in the
table for a directive that names one by word; they are not the company's lines.
"""

from __future__ import annotations

__all__ = ["COMPANY_LINES", "DEFAULT_LINE", "LINES", "menu", "recipe"]

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
        "sells": "Шаблоны и исходники мобильных приложений: 1 500–6 000 ₽ за проект; витрина — ВК и Telegram, "
        "сообщества разработчиков и заказчиков мобильных приложений.",
    },
    "pwa": {
        "name": "Мобильные веб-приложения (PWA)",
        "makes": "Готовое устанавливаемое мобильное приложение на HTML, CSS и JavaScript без сборки: index.html, "
        "manifest.json (имя, иконки, цвета, display standalone), service worker с кэшем всех файлов для "
        "работы без сети, экраны и логика в файлах проекта, README: что делает, как открыть с телефона и "
        "поставить на экран «Домой» (Android и iOS), как выложить на любой статический хостинг. Одна "
        "страница не бывает приложением: минимум манифест, service worker, иконки и дописанные экраны. "
        "Делается здесь целиком — единственная линия, где покупатель получает приложение, которое ставит сразу.",
        "ready": "manifest.json — валидный JSON, согласованный с index.html (имя, start_url, иконки); service "
        "worker регистрируется из index.html и кэширует все файлы приложения; экраны и логика дописаны, в коде "
        "нет заглушек «TODO»; README объясняет установку на экран «Домой» за пять шагов.",
        "sells": "Готовые мобильные приложения и их исходники: 1 000–5 000 ₽ за приложение, доработка под "
        "заказчика — дороже; витрина — ВК и Telegram, малый бизнес и авторы, которым нужно приложение без магазина.",
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

# The company's own lines (owner, 09.11.2026: a mobile app company): what the founder
# picks from and what the director sees. The other recipes stay for a directive that
# names one by word.
COMPANY_LINES: tuple[str, ...] = ("android", "pwa")

DEFAULT_LINE = "android"


def recipe(line: str | None) -> str:
    """The route card of a line, for a role's prompt."""
    spec = LINES.get(line or "", LINES[DEFAULT_LINE])
    return (
        f"Линия: {spec['name']}.\nЧто делаем: {spec['makes']}\nГотово, когда: {spec['ready']}\n"
        f"Где и за сколько продаётся: {spec['sells']}"
    )


def menu() -> str:
    """The company's lines, for the founder's and the director's prompts."""
    return "\n".join(
        f"- {key}: {LINES[key]['name']} — {LINES[key]['sells']}" for key in COMPANY_LINES
    )

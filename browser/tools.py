"""Browser tools of the shared tool registry (category ``browser``, also over MCP).

``BROWSER_TOOLS`` holds 17 tool definitions with strict JSON schemas and Russian
descriptions. Each tool's function runs one operation of ``browser.operations``
in the caller's browser session (the dialog's, "default" over MCP, or the
explicit ``session``) and returns its data with ``success`` True; an expected
failure (``BrowserError``) becomes a failed result whose Russian message reaches
the model. The operations and sessions modules load on the first call, so
listing the tools imports neither ``requests`` nor Chrome support.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Mapping

from browser._vendor.schema import object_schema
from browser.context import CallContext
from browser.errors import E_APPROVAL_REQUIRED, BrowserError

logger = logging.getLogger("logos.browser.tools")

_VIEWS = [
    "content",
    "page",
    "text",
    "html",
    "outline",
    "links",
    "interactive",
    "forms",
    "tables",
    "images",
    "meta",
    "structured",
    "feed",
    "headers",
    "json",
    "network",
    "console",
]
_TRANSPORTS = ["responses_api", "local_agent", "mcp"]
_RESULT_REDACT = ["password", "authorization", "set_cookie", "cookie_value", "proxy_authorization"]


def _opt(schema: Mapping[str, Any]) -> dict[str, Any]:
    """An optional property: the schema itself (description inside) or null."""
    return {"anyOf": [dict(schema), {"type": "null"}]}


def _with(schema: Mapping[str, Any], description: str) -> dict[str, Any]:
    """A copy of a shared property schema with its own description."""
    return {**schema, "description": description}


def _string(description: str) -> dict[str, Any]:
    return {"type": "string", "minLength": 1, "description": description}


def _boolean(description: str) -> dict[str, Any]:
    return {"type": "boolean", "description": description}


def _pairs(description: str) -> dict[str, Any]:
    return {
        "type": "array",
        "description": description,
        "items": object_schema(
            {"name": {"type": "string", "minLength": 1}, "value": {"type": "string"}},
            ["name", "value"],
        ),
    }


SESSION = _string(
    "Имя браузерной сессии; не указывайте — сессия текущего диалога (через MCP — общая «default»)"
)
TAB = _string("Вкладка (t1, t2…); не указана — текущая")
MAX_CHARS = {
    "type": "integer",
    "minimum": 0,
    "description": "Сколько символов текста вернуть; 0 — всё без ограничения. "
    "Продолжение — тем же вызовом с offset=next_offset",
}
MAX_CHARS_OPT = _with(
    MAX_CHARS,
    "Сколько символов текста вернуть; не указано или 0 — всё. "
    "Продолжение — тем же вызовом с offset=next_offset",
)
OFFSET = {
    "type": "integer",
    "minimum": 0,
    "description": "С какого символа продолжить (next_offset прошлого ответа)",
}
TIMEOUT = {
    "type": "number",
    "minimum": 0,
    "description": "Предел времени всей операции, секунды; 0 или не указано — без ограничения",
}
MAX_REDIRECTS = {
    "type": "integer",
    "minimum": 0,
    "description": "Предел перенаправлений; 0 или не указано — без ограничения (циклы распознаются)",
}
MAX_BYTES = {
    "type": "integer",
    "minimum": 0,
    "description": "Сколько байт ответа загрузить; 0 или не указано — без ограничения; "
    "обрезка всегда сообщается",
}
ENGINE = {
    "type": "string",
    "enum": ["auto", "http", "chrome"],
    "description": "Движок: http — быстрый, без JavaScript; chrome — настоящий Chrome с "
    "JavaScript (если установлен); auto — http, а страницы, собираемые скриптами, — в "
    "chrome. Запоминается для вкладки",
}
VIEW = {
    "type": "string",
    "enum": list(_VIEWS),
    "description": "Вид: content — основной текст (по умолчанию); page — вся страница; "
    "text — простой текст; html — исходный код; outline — оглавление с номерами "
    "разделов; links — ссылки; interactive — ссылки, кнопки и поля; forms — формы и "
    "поля; tables — таблицы Markdown; images — изображения; meta — title, description, "
    "og:*, canonical, ленты; structured — JSON-LD и микроданные; feed — RSS/Atom; "
    "headers — HTTP-запрос и ответ; json — JSON; network и console — запросы и консоль "
    "страницы (chrome)",
}
NAV_VIEW = {
    "type": "string",
    "enum": list(_VIEWS) + ["none"],
    "description": "Что вернуть после перехода: вид как в browser_read или none — только "
    "сводка; не указан — content",
}
ACTION_VIEW = _with(
    NAV_VIEW,
    "Что вернуть: вид как в browser_read или none — только сводка; не указан — content "
    "после перехода, иначе список изменений",
)
WAIT_UNTIL = {
    "type": "string",
    "enum": ["commit", "domcontentloaded", "load", "networkidle", "networkidle2"],
    "description": "Только chrome: чего ждать после перехода; не указано — load",
}
REF = _string(
    "Метка элемента из текста страницы: L5 — ссылка, B2 — кнопка, I3 — поле, F1 — "
    "форма, T1 — таблица, M4 — изображение"
)
TEXT_T = _string("Видимый текст элемента, если метки нет")
SELECTOR_T = _string("CSS-селектор элемента (в http — подмножество, в chrome — полный CSS)")
NTH = {
    "type": "integer",
    "minimum": 1,
    "description": "Какое по счёту совпадение взять (с 1)",
}
OVERWRITE = _boolean("Перезаписать существующий файл")
INCLUDE_HIDDEN = _boolean("Показать и скрытые элементы")


def _approved(cfg: Mapping[str, Any] | None) -> bool:
    """The caller's approval flag, read from the universal call in cfg["_universal_context"]."""
    context = cfg.get("_universal_context") if isinstance(cfg, Mapping) else None
    call = context.get("call") if isinstance(context, Mapping) else None
    return bool(getattr(call, "approved", False)) if call is not None else False


def _handler(
    name: str,
    requires_approval: bool = False,
    approval_when: Callable[[Mapping[str, Any]], bool] | None = None,
) -> Callable[..., dict[str, Any]]:
    """The registry function of one tool: run its operation, map BrowserError to a result."""

    def run(arguments: Mapping[str, Any] | None, cfg: Mapping[str, Any] | None = None) -> dict[str, Any]:
        gated = requires_approval or (approval_when is not None and approval_when(arguments or {}))
        if gated and not _approved(cfg):
            return BrowserError(E_APPROVAL_REQUIRED.format(tool=name), kind="approval_required").to_result()
        args = dict(arguments or {})
        ctx = CallContext(name, args, cfg)
        try:
            from browser import operations, sessions

            operation, locked = operations.OPERATIONS[name]
            if locked:
                data = sessions.run_operation(ctx, operation, args)
            else:
                data = operation(None, args, ctx)
        except BrowserError as exc:
            logger.debug("%s failed: %s", name, exc.kind)
            return exc.to_result()
        return {"success": True, **data}

    run.__name__ = name
    run.__qualname__ = name
    return run


def _tool(
    name: str,
    *,
    title: str,
    description: str,
    properties: Mapping[str, Mapping[str, Any]],
    required: list[str],
    read_only: bool,
    risk: str,
    redact_args: tuple[str, ...] | list[str] = (),
    requires_chrome: bool = False,
    requires_approval: bool = False,
    approval_when: Callable[[Mapping[str, Any]], bool] | None = None,
) -> dict[str, Any]:
    """One registry definition; every property not in ``required`` accepts null."""
    shaped = {
        key: dict(schema) if key in required else _opt(schema)
        for key, schema in properties.items()
    }
    return {
        "title": title,
        "description": description,
        "parameters": object_schema(shaped, list(required)),
        "capabilities": ["browser"],
        "risk_level": risk,
        "read_only": read_only,
        "requires_approval": requires_approval,
        "supported_transports": list(_TRANSPORTS),
        "executor": {"type": "local"},
        "metadata": {
            "engines": ["http", "chrome"],
            "requires_chrome": requires_chrome,
            "credentials_model_visible": False,
            "real_profile_access": False,
            "trace_redact_arguments": list(redact_args),
            "trace_redact_result_fields": list(_RESULT_REDACT),
        },
        "func": _handler(name, requires_approval, approval_when),
    }


BROWSER_TOOLS: dict[str, dict[str, Any]] = {
    "browser_open": _tool(
        "browser_open",
        title="Браузер: открыть адрес",
        description=(
            "Открыть адрес во вкладке браузера Logos и сразу получить читаемый текст "
            "страницы. У ссылок, кнопок, полей, форм, таблиц и изображений стоят метки "
            "[L1] [B1] [I1] [F1] [T1] [M1] — передавайте их в browser_click, "
            "browser_fill_form, browser_act, browser_download. Длинные страницы читаются "
            "частями: next_offset → browser_read(offset=…). Файлы (PDF, архивы) не "
            "загружаются — предлагается browser_download. У каждого диалога своя сессия: "
            "cookies, вкладки и история сохраняются между вызовами."
        ),
        properties={
            "url": _string("Адрес: http(s)://…, file://… или data:…"),
            "max_chars": MAX_CHARS,
            "session": SESSION,
            "tab": TAB,
            "new_tab": _boolean("Открыть в новой вкладке"),
            "engine": ENGINE,
            "view": NAV_VIEW,
            "headers": _pairs("Дополнительные заголовки этого запроса"),
            "wait_until": WAIT_UNTIL,
            "timeout_seconds": TIMEOUT,
            "max_redirects": MAX_REDIRECTS,
            "max_bytes": MAX_BYTES,
        },
        required=["url", "max_chars"],
        read_only=True,
        risk="low",
        redact_args=("headers",),
    ),
    "browser_read": _tool(
        "browser_read",
        title="Браузер: прочитать страницу",
        description=(
            "Прочитать текущую страницу вкладки в нужном виде (view). selector или ref "
            "ограничивают область, section — раздел из outline, filter отбирает строки "
            "списков. Сеть не используется: читается уже открытая страница (в chrome — её "
            "текущее состояние)."
        ),
        properties={
            "max_chars": MAX_CHARS,
            "session": SESSION,
            "tab": TAB,
            "view": VIEW,
            "offset": OFFSET,
            "selector": _string("CSS-селектор: показать только эти элементы"),
            "ref": _string("Показать только этот элемент: таблицу T2, форму F1, запрос N5"),
            "section": _string("Номер раздела из outline (например 2.1) или текст заголовка"),
            "filter": _string(
                "Отбор строк для links, images, interactive, network, console: подстрока "
                "без учёта регистра или /регулярное выражение/"
            ),
            "inline_urls": _boolean("Показывать адреса ссылок прямо в тексте"),
            "json_path": _string(
                "Путь в JSON: data.items[0].name, items[*].id, $['ключ'] или JSON Pointer /items/0"
            ),
            "include_hidden": INCLUDE_HIDDEN,
        },
        required=["max_chars"],
        read_only=True,
        risk="low",
    ),
    "browser_find": _tool(
        "browser_find",
        title="Браузер: найти на странице",
        description=(
            "Найти текст на текущей странице: для каждого совпадения — раздел, ближайшая "
            "метка элемента и смещение для browser_read(offset=…); совпадение выделено **так**."
        ),
        properties={
            "query": _string("Что искать"),
            "max_chars": MAX_CHARS,
            "session": SESSION,
            "tab": TAB,
            "regex": _boolean("query — регулярное выражение Python"),
            "case_sensitive": _boolean("Учитывать регистр"),
            "view": {
                "type": "string",
                "enum": ["page", "content", "text", "html"],
                "description": "Где искать; не указано — page (вся страница)",
            },
            "context_chars": {
                "type": "integer",
                "minimum": 0,
                "description": "Символов контекста с каждой стороны; не указано — весь абзац",
            },
            "offset": OFFSET,
        },
        required=["query", "max_chars"],
        read_only=True,
        risk="low",
    ),
    "browser_extract": _tool(
        "browser_extract",
        title="Браузер: извлечь по CSS",
        description=(
            "Извлечь элементы текущей страницы по CSS-селектору. Движок http понимает: "
            "тег, *, #id, .class, [attr], [attr=v], [attr~=v], [attr|=v], [attr^=v], "
            "[attr$=v], [attr*=v], пробел, >, +, ~, запятую, :nth-child(), "
            ":nth-last-child(), :nth-of-type(), :first-child, :last-child, :only-child, "
            ":first-of-type, :last-of-type, :not(), :empty, :root, :checked, :disabled, "
            ":enabled, :contains(\"текст\"); в chrome — полный CSS."
        ),
        properties={
            "selector": _string("CSS-селектор"),
            "max_chars": MAX_CHARS,
            "session": SESSION,
            "tab": TAB,
            "output": {
                "type": "string",
                "enum": ["text", "markdown", "html", "attributes"],
                "description": "text — текст (по умолчанию), markdown, html — разметка, "
                "attributes — атрибуты",
            },
            "attributes": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
                "description": "Какие атрибуты показать; не указано — все",
            },
            "include_hidden": INCLUDE_HIDDEN,
            "offset": OFFSET,
        },
        required=["selector", "max_chars"],
        read_only=True,
        risk="low",
    ),
    "browser_click": _tool(
        "browser_click",
        title="Браузер: нажать",
        description=(
            "Нажать на элемент: ссылку, кнопку отправки формы, флажок, переключатель, "
            "кнопку на JavaScript (chrome). Цель — ref, видимый text или selector; в "
            "chrome также x и y. После перехода — новая страница, без перехода — список "
            "изменений."
        ),
        properties={
            "max_chars": MAX_CHARS,
            "session": SESSION,
            "tab": TAB,
            "ref": REF,
            "text": TEXT_T,
            "selector": SELECTOR_T,
            "nth": NTH,
            "x": {
                "type": "number",
                "description": "Только chrome: координата X в окне (CSS-пиксели)",
            },
            "y": {
                "type": "number",
                "description": "Только chrome: координата Y в окне (CSS-пиксели)",
            },
            "new_tab": _boolean("Открыть ссылку или результат формы в новой вкладке"),
            "force": _boolean(
                "Только chrome: нажать через JavaScript, даже если элемент перекрыт или не виден"
            ),
            "view": ACTION_VIEW,
            "wait_until": WAIT_UNTIL,
            "timeout_seconds": TIMEOUT,
            "max_redirects": MAX_REDIRECTS,
            "max_bytes": MAX_BYTES,
        },
        required=["max_chars"],
        read_only=False,
        risk="medium",
    ),
    "browser_fill_form": _tool(
        "browser_fill_form",
        title="Браузер: заполнить форму",
        description=(
            "Заполнить форму и (submit=true) отправить её. Скрытые поля сохраняются; GET "
            "и POST, urlencoded и multipart (файлы), списки, флажки, переключатели. "
            "Значения паролей не попадают в журналы."
        ),
        properties={
            "fields": {
                "type": "array",
                "minItems": 1,
                "description": "Поля для заполнения",
                "items": object_schema(
                    {
                        "field": _string(
                            "Поле: метка I3, name, подпись, placeholder или CSS-селектор"
                        ),
                        "value": _opt(
                            {
                                "type": "string",
                                "description": "Текст или вариант списка (value или текст "
                                "варианта); для группы флажков/переключателей — value нужного",
                            }
                        ),
                        "values": _opt(
                            {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Несколько вариантов списка multiple",
                            }
                        ),
                        "checked": _opt(_boolean("Флажок/переключатель: отметить или снять")),
                        "file_paths": _opt(
                            {
                                "type": "array",
                                "items": {"type": "string", "minLength": 1},
                                "description": "Файлы для поля загрузки (пути как в "
                                "файловых инструментах)",
                            }
                        ),
                    },
                    ["field"],
                ),
            },
            "max_chars": MAX_CHARS,
            "session": SESSION,
            "tab": TAB,
            "form": _string("Форма: метка F1 или CSS-селектор; не указана — форма первого поля"),
            "submit": _boolean("Отправить форму после заполнения"),
            "submit_button": _string(
                "Кнопка отправки: метка B2 или её текст; не указана — первая кнопка отправки"
            ),
            "view": ACTION_VIEW,
            "wait_until": WAIT_UNTIL,
            "timeout_seconds": TIMEOUT,
            "max_redirects": MAX_REDIRECTS,
            "max_bytes": MAX_BYTES,
        },
        required=["fields", "max_chars"],
        read_only=False,
        risk="medium",
        redact_args=("fields",),
    ),
    "browser_act": _tool(
        "browser_act",
        title="Браузер: действие с элементом",
        description=(
            "Действие с элементом страницы. В движке http работают type, select, check, "
            "uncheck, clear, focus и press Enter (отправка формы); hover, scroll и прочие "
            "клавиши — в chrome. Возвращает список изменений или новую страницу после "
            "перехода."
        ),
        properties={
            "action": {
                "type": "string",
                "enum": [
                    "type",
                    "press",
                    "hover",
                    "scroll",
                    "select",
                    "check",
                    "uncheck",
                    "focus",
                    "clear",
                ],
                "description": "type — ввести value (прежний текст заменяется); press — "
                "клавиши (Enter, Tab, Escape, ArrowDown, Control+A); hover — навести; "
                "scroll — прокрутить (value: down, up, top, bottom, left, right или "
                "пиксели, например 800); select — варианты списка (несколько — через "
                "перевод строки); check/uncheck — флажок; focus; clear",
            },
            "max_chars": MAX_CHARS,
            "session": SESSION,
            "tab": TAB,
            "ref": REF,
            "text": TEXT_T,
            "selector": SELECTOR_T,
            "nth": NTH,
            "value": {
                "type": "string",
                "description": "Текст, клавиши, прокрутка или вариант — по action",
            },
            "submit": _boolean("Для type: нажать Enter после ввода"),
            "view": ACTION_VIEW,
            "wait_until": WAIT_UNTIL,
            "timeout_seconds": TIMEOUT,
        },
        required=["action", "max_chars"],
        read_only=False,
        risk="medium",
        redact_args=("value",),
    ),
    "browser_wait": _tool(
        "browser_wait",
        title="Браузер: подождать",
        description=(
            "Подождать на странице chrome по очереди: адреса, элемента, текста, "
            "исчезновения текста, выражения, затишья сети, затем seconds. Во вкладке http "
            "доступна только пауза seconds."
        ),
        properties={
            "session": SESSION,
            "tab": TAB,
            "selector": _string("Дождаться элемента (CSS-селектор)"),
            "selector_state": {
                "type": "string",
                "enum": ["visible", "attached", "hidden", "detached"],
                "description": "Состояние элемента; не указано — visible",
            },
            "text": _string("Дождаться текста"),
            "text_gone": _string("Дождаться исчезновения текста"),
            "url_contains": _string("Дождаться адреса со строкой"),
            "expression": _string("Дождаться истинности выражения JavaScript"),
            "network_idle": _boolean("Дождаться затишья сети на 0,5 с"),
            "seconds": {
                "type": "number",
                "minimum": 0,
                "description": "Просто подождать столько секунд",
            },
            "timeout_seconds": TIMEOUT,
        },
        required=[],
        read_only=True,
        risk="low",
        requires_chrome=True,
        # A JavaScript expression runs in the page, so it needs the same approval as browser_evaluate.
        approval_when=lambda args: bool(args.get("expression")),
    ),
    "browser_evaluate": _tool(
        "browser_evaluate",
        title="Браузер: выполнить JavaScript",
        description=(
            "Выполнить JavaScript на странице chrome; результат — JSON (DOM-узлы "
            "описываются тегом и текстом) и сообщения консоли. Большой результат читается "
            "частями через result_id и offset."
        ),
        properties={
            "max_chars": MAX_CHARS,
            "session": SESSION,
            "tab": TAB,
            "expression": _string(
                "JavaScript: выражение (можно await) или, с ref/selector, функция (el) => …"
            ),
            "ref": REF,
            "selector": SELECTOR_T,
            "await_promise": _boolean("Ждать результата Promise; не указано — да"),
            "result_id": _string("Дочитать сохранённый результат (E1…) без повторного выполнения"),
            "offset": OFFSET,
            "timeout_seconds": TIMEOUT,
        },
        required=["max_chars"],
        read_only=False,
        risk="high",
        requires_chrome=True,
        requires_approval=True,
    ),
    "browser_screenshot": _tool(
        "browser_screenshot",
        title="Браузер: снимок или PDF",
        description=(
            "Сохранить снимок страницы chrome (PNG/JPEG: окно, вся страница или элемент) "
            "или печать в PDF. Возвращает путь, размер файла и свободное место на диске."
        ),
        properties={
            "path": _string("Файл или папка; относительный путь — от корня проекта"),
            "overwrite": OVERWRITE,
            "session": SESSION,
            "tab": TAB,
            "format": {
                "type": "string",
                "enum": ["png", "jpeg", "pdf"],
                "description": "Формат; не указан — по расширению path",
            },
            "full_page": _boolean("Вся страница, а не только окно"),
            "ref": REF,
            "selector": SELECTOR_T,
            "quality": {
                "type": "integer",
                "minimum": 0,
                "maximum": 100,
                "description": "Качество JPEG",
            },
            "landscape": _boolean("PDF: альбомная ориентация"),
            "print_background": _boolean("PDF: печатать фон"),
            "paper": {
                "type": "string",
                "enum": ["A3", "A4", "A5", "Letter", "Legal", "Tabloid"],
                "description": "PDF: формат бумаги; не указан — как задаёт страница",
            },
            "scale": {
                "type": "number",
                "exclusiveMinimum": 0,
                "description": "PDF: масштаб (Chrome принимает 0,1–2)",
            },
            "page_ranges": _string("PDF: страницы, например 1-3,5"),
            "timeout_seconds": TIMEOUT,
        },
        required=["path", "overwrite"],
        read_only=False,
        risk="medium",
        requires_chrome=True,
    ),
    "browser_history": _tool(
        "browser_history",
        title="Браузер: история вкладки",
        description=(
            "История вкладки. back и forward в движке http показывают сохранённую копию "
            "без запроса; reload повторяет запрос (POST отправляется заново); list — "
            "переходы вкладки."
        ),
        properties={
            "action": {
                "type": "string",
                "enum": ["back", "forward", "reload", "list"],
                "description": "back, forward — назад и вперёд; reload — обновить; list — история",
            },
            "max_chars": MAX_CHARS,
            "session": SESSION,
            "tab": TAB,
            "steps": {
                "type": "integer",
                "minimum": 1,
                "description": "На сколько шагов; не указано — 1",
            },
            "view": NAV_VIEW,
            "wait_until": WAIT_UNTIL,
            "timeout_seconds": TIMEOUT,
            "max_redirects": MAX_REDIRECTS,
            "max_bytes": MAX_BYTES,
        },
        required=["action", "max_chars"],
        read_only=False,
        risk="medium",
    ),
    "browser_tabs": _tool(
        "browser_tabs",
        title="Браузер: вкладки",
        description=(
            "Вкладки браузера диалога: list, new (url — сразу открыть, engine — движок), "
            "switch, close (закрытие последней вкладки chrome останавливает Chrome, "
            "cookies сохраняются)."
        ),
        properties={
            "action": {
                "type": "string",
                "enum": ["list", "new", "switch", "close"],
                "description": "list — список вкладок; new — новая вкладка; switch — сделать "
                "вкладку tab текущей; close — закрыть вкладку tab (не указана — текущую)",
            },
            "session": SESSION,
            "tab": _string("Вкладка для switch и close (t1, t2…); для close не указана — текущая"),
            "url": _string("Для new: сразу открыть адрес"),
            "engine": ENGINE,
            "view": _with(
                NAV_VIEW,
                "Для new с url и для switch: вид страницы как в browser_read или none — "
                "только сводка; не указан — none",
            ),
            "max_chars": MAX_CHARS_OPT,
        },
        required=["action"],
        read_only=False,
        risk="low",
    ),
    "browser_session": _tool(
        "browser_session",
        title="Браузер: сессия и настройки",
        description=(
            "Сессии браузера. list — все сессии; status — вкладки, Chrome, cookies, "
            "загрузки, свободное место; configure — User-Agent, язык, заголовки, движок, "
            "окно chrome, JavaScript, изображения, диалоги, прокси; clear — "
            "освободить память (сохранённые ответы и копии страниц); reset — закрыть всё и "
            "начать с чистого профиля (настройки сохраняются); close — удалить сессию."
        ),
        properties={
            "action": {
                "type": "string",
                "enum": ["list", "status", "configure", "clear", "reset", "close"],
                "description": "list, status, configure, clear, reset или close",
            },
            "session": SESSION,
            "all": _boolean("Для reset/close: все сессии"),
            "user_agent": {
                "type": "string",
                "description": "desktop, mobile, своя строка или пустая — стандартный",
            },
            "accept_language": _string("Заголовок Accept-Language, например ru-RU,ru;q=0.9,en;q=0.8"),
            "headers": _pairs(
                "Доп. заголовки для всех запросов; заменяют прежние; пустой список — убрать"
            ),
            "default_engine": _with(ENGINE, "Движок новых вкладок: auto, http или chrome"),
            "viewport_width": {
                "type": "integer",
                "minimum": 1,
                "description": "Ширина окна chrome, CSS-пиксели",
            },
            "viewport_height": {
                "type": "integer",
                "minimum": 1,
                "description": "Высота окна chrome, CSS-пиксели",
            },
            "device_scale_factor": {
                "type": "number",
                "exclusiveMinimum": 0,
                "description": "Плотность пикселей окна chrome (1 — обычная, 2 — Retina)",
            },
            "mobile": _boolean("Мобильный режим chrome (сенсорный ввод, мобильная вёрстка)"),
            "javascript": _boolean("Выполнять JavaScript в chrome"),
            "load_images": _boolean("Загружать изображения в chrome"),
            "dialogs": {
                "type": "string",
                "enum": ["accept", "dismiss"],
                "description": "Ответ на alert/confirm/prompt",
            },
            "dialog_prompt_text": {"type": "string", "description": "Текст ответа на prompt"},
            "proxy": {
                "type": "string",
                "description": "Прокси http://host:port; пустая строка — из окружения",
            },
            "offset": OFFSET,
            "max_chars": MAX_CHARS_OPT,
        },
        required=["action"],
        read_only=False,
        risk="medium",
        redact_args=("headers", "proxy"),
    ),
    "browser_cookies": _tool(
        "browser_cookies",
        title="Браузер: cookies",
        description=(
            "Cookies сессии (общие для http и chrome): list — имена, домены, атрибуты, "
            "длина и отпечаток значения (значения не показываются); set; delete по "
            "name/domain; clear. В browser_request значение подставляется без раскрытия: "
            "{{cookie:имя}}."
        ),
        properties={
            "action": {
                "type": "string",
                "enum": ["list", "set", "delete", "clear"],
                "description": "list — список; set — установить; delete — удалить по name/domain; "
                "clear — удалить все (или домена domain)",
            },
            "session": SESSION,
            "domain": _string("Домен: отбор для list/delete/clear, домен cookie для set"),
            "url": _string("Адрес, для которого установить cookie (вместо domain)"),
            "name": _string("Имя cookie"),
            "value": {"type": "string", "description": "Значение cookie (для set)"},
            "path": _string("Путь; не указан — /"),
            "secure": _boolean("Только по HTTPS"),
            "http_only": _boolean("Недоступна JavaScript страницы (HttpOnly)"),
            "same_site": {
                "type": "string",
                "enum": ["Strict", "Lax", "None"],
                "description": "Атрибут SameSite",
            },
            "expires": {
                "type": "number",
                "description": "Срок, секунды Unix; не указан — до конца сессии",
            },
            "offset": OFFSET,
            "max_chars": MAX_CHARS_OPT,
        },
        required=["action"],
        read_only=False,
        risk="high",
        redact_args=("value",),
        requires_approval=True,
    ),
    "browser_search": _tool(
        "browser_search",
        title="Браузер: поиск в интернете",
        description=(
            "Поиск в интернете с разбором результатов: номер, заголовок, адрес, фрагмент. "
            "Реклама не включается (число — в notes). Открыть результат: browser_open(url=…)."
        ),
        properties={
            "query": _string("Поисковый запрос"),
            "engine": {
                "type": "string",
                "enum": ["duckduckgo", "duckduckgo_lite", "bing", "all", "custom"],
                "description": "duckduckgo, duckduckgo_lite, bing, all — DuckDuckGo и Bing без "
                "повторов, custom — свой поисковик",
            },
            "max_chars": MAX_CHARS,
            "session": SESSION,
            "page": {
                "type": "integer",
                "minimum": 1,
                "description": "Страница результатов; не указана — 1",
            },
            "region": _string("Язык-СТРАНА: ru-RU, en-US"),
            "safe_search": {
                "type": "string",
                "enum": ["off", "moderate", "strict"],
                "description": "Безопасный поиск",
            },
            "time_range": {
                "type": "string",
                "enum": ["day", "week", "month", "year"],
                "description": "Только результаты за последний день, неделю, месяц или год",
            },
            "tab": _string(
                "Загрузить страницу результатов во вкладку: её id или new — тогда ссылки "
                "можно нажимать"
            ),
            "url_template": _string(
                "custom: адрес с {query} и при необходимости {page}, {offset}"
            ),
            "result_selector": _string("custom: CSS-селектор блока одного результата"),
            "title_selector": _string("custom: CSS-селектор заголовка внутри блока"),
            "link_selector": _string("custom: CSS-селектор ссылки внутри блока"),
            "snippet_selector": _string("custom: CSS-селектор фрагмента текста внутри блока"),
            "page_size": {
                "type": "integer",
                "minimum": 1,
                "description": "custom: результатов на странице для {offset}",
            },
            "offset": OFFSET,
            "timeout_seconds": TIMEOUT,
        },
        required=["query", "engine", "max_chars"],
        read_only=True,
        risk="low",
    ),
    "browser_request": _tool(
        "browser_request",
        title="Браузер: HTTP-запрос",
        description=(
            "Произвольный HTTP-запрос для API и форм: любой метод, заголовки, query, тело "
            "JSON, форма, multipart с файлами или сырое тело. {{cookie:имя}} в заголовках и "
            "теле подставляет cookie сессии, не раскрывая её. Ответ: статус, "
            "перенаправления, заголовки (секреты скрыты) и тело частями (JSON красиво, "
            "json_path — выборка); save_to — потоково в файл; tab — во вкладку для "
            "browser_read/find/extract."
        ),
        properties={
            "max_chars": MAX_CHARS,
            "session": SESSION,
            "method": _string(
                "GET, POST, PUT, PATCH, DELETE, HEAD, OPTIONS…; не указан — GET"
            ),
            "url": _string("Адрес запроса http(s)://…"),
            "headers": _pairs("Заголовки; пустое value убирает стандартный"),
            "query": _pairs("Параметры строки запроса"),
            "json_body": {"type": "string", "description": "Тело JSON строкой"},
            "form": _pairs("Поля формы (urlencoded; с files — multipart)"),
            "files": {
                "type": "array",
                "description": "Файлы multipart: имя поля и путь (как в файловых инструментах)",
                "items": object_schema(
                    {
                        "name": _string("Имя поля формы"),
                        "path": _string("Путь к файлу"),
                        "filename": _opt(_string("Имя файла в запросе; не указано — из пути")),
                        "content_type": _opt(
                            _string("Content-Type файла; не указан — по расширению")
                        ),
                    },
                    ["name", "path"],
                ),
            },
            "body": {"type": "string", "description": "Сырое тело (UTF-8)"},
            "body_base64": {"type": "string", "description": "Сырое двоичное тело в base64"},
            "use_cookies": _boolean("Отправлять и сохранять cookies сессии; не указано — да"),
            "follow_redirects": _boolean("Следовать перенаправлениям; не указано — да"),
            "max_redirects": MAX_REDIRECTS,
            "max_bytes": MAX_BYTES,
            "timeout_seconds": TIMEOUT,
            "tab": _string("Загрузить ответ во вкладку: id или new"),
            "save_to": _string("Сохранить тело в файл потоково: файл или папка"),
            "overwrite": _with(
                OVERWRITE, "Перезаписать существующий файл (для save_to); не указано — нет"
            ),
            "response_id": _string("Дочитать сохранённый ответ (R1…) без нового запроса"),
            "json_path": _string(
                "Путь в JSON: data.items[0].name, items[*].id, $['ключ'] или JSON Pointer /items/0"
            ),
            "offset": OFFSET,
        },
        required=["max_chars"],
        read_only=False,
        risk="high",
        redact_args=("headers", "body", "body_base64", "json_body", "form"),
        requires_approval=True,
    ),
    "browser_download": _tool(
        "browser_download",
        title="Браузер: скачать файл",
        description=(
            "Скачать файл потоком прямо на диск (в память не загружается): по url, по "
            "метке ссылки/изображения или текущий документ вкладки (POST отправляется "
            "заново). С cookies и Referer сессии. Возвращает путь, размер, SHA-256 и "
            "свободное место."
        ),
        properties={
            "path": _string(
                "Файл или папка (существующая или оканчивающаяся на /); имя — из ответа "
                "сервера или адреса"
            ),
            "overwrite": OVERWRITE,
            "session": SESSION,
            "url": _string("Адрес файла http(s)://…, data:… или file://…"),
            "ref": _string("Метка ссылки или изображения: L5, M2"),
            "tab": TAB,
            "use_cookies": _boolean("Отправлять и сохранять cookies сессии; не указано — да"),
            "max_bytes": MAX_BYTES,
            "max_redirects": MAX_REDIRECTS,
            "timeout_seconds": TIMEOUT,
        },
        required=["path", "overwrite"],
        read_only=False,
        risk="high",
    ),
}

__all__ = ["BROWSER_TOOLS"]

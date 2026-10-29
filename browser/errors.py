"""Errors, deadlines and the Russian message catalog of the browser tools.

Every expected failure of a browser tool is a ``BrowserError`` (a ``ValueError``)
with a ``kind``; ``to_result`` turns it into the failed-tool mapping the tool
registry understands, so the Russian text reaches the model on the chat path and
over MCP. Catalog messages are ``str.format`` templates with named fields; a
field the caller does not pass renders as an empty string instead of raising.
"""

from __future__ import annotations

import logging
import math
import re
import time
from typing import Any, Mapping

logger = logging.getLogger("logos.browser.errors")

# kind -> metadata phase; the kinds marked as lookups also carry error_type.
_PHASES = {
    "invalid": "validation",
    "unavailable": "validation",
    "not_found": "execution",
    "network": "execution",
    "timeout": "execution",
    "blocked": "authorization",
    "engine": "execution",
    "approval_required": "approval_required",
}
_LOOKUP_KINDS = frozenset({"not_found", "network", "timeout"})
_LINE_BREAKS = re.compile(r"[\r\n\u2028\u2029]+")


def single_line(text: Any) -> str:
    """Collapse line breaks to "; " so a message stays one line."""
    parts = [part.strip() for part in _LINE_BREAKS.split(str(text))]
    return "; ".join(part for part in parts if part)


class BrowserError(ValueError):
    """An expected browser-tool failure carrying a Russian, single-line message."""

    kind = "invalid"

    def __init__(
        self,
        message: str,
        *,
        kind: str | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(single_line(message))
        if kind is not None:
            self.kind = kind
        self.details: dict[str, Any] = dict(details or {})

    def to_result(self) -> dict[str, Any]:
        """The failed tool result: success False, the message and phase metadata."""
        metadata: dict[str, Any] = {
            "phase": _PHASES.get(self.kind, "execution"),
            "browser_error": self.kind,
        }
        if self.kind in _LOOKUP_KINDS:
            metadata["error_type"] = "LookupError"
        metadata.update(self.details)
        return {"success": False, "error": str(self), "metadata": metadata}


class BrowserNotFound(BrowserError):
    kind = "not_found"


class BrowserNetworkError(BrowserError):
    kind = "network"


class BrowserTimeout(BrowserError):
    kind = "timeout"


class BrowserBlocked(BrowserError):
    kind = "blocked"


class BrowserUnavailable(BrowserError):
    kind = "unavailable"


# ---------------------------------------------------------------------------
# Message templates


class _Missing:
    """Stands in for a template field the caller did not pass."""

    __slots__ = ()

    def __format__(self, spec: str) -> str:
        return ""

    def __str__(self) -> str:
        return ""


_MISSING = _Missing()


class _Values(dict):
    def __missing__(self, key: str) -> _Missing:
        return _MISSING


class Message(str):
    """A catalog template: ``format`` leaves out fields that were not passed."""

    __slots__ = ()

    def format(self, /, *args: Any, **kwargs: Any) -> str:
        if args:
            return str.format(self, *args, **kwargs)
        return str.format_map(self, _Values(kwargs))

    def format_map(self, mapping: Mapping[str, Any]) -> str:  # type: ignore[override]
        return str.format_map(self, _Values(mapping))


class _Literal(Message):
    """A catalog text without fields: its braces are literal, format returns it."""

    __slots__ = ()

    def format(self, /, *args: Any, **kwargs: Any) -> str:
        return str(self)

    def format_map(self, mapping: Mapping[str, Any]) -> str:  # type: ignore[override]
        return str(self)


def message(template: str, **values: Any) -> str:
    """Format a catalog template, rendering missing fields as empty strings."""
    return str.format_map(template, _Values(values))


# Identical to tool_providers.filesystem.http_fetch.
LINK_LOCAL_MESSAGE = "Адреса link-local, включая метаданные облака, недоступны"

CHROME_INSTALL_HINT = (
    "Установите Chromium: Termux — pkg install x11-repo && pkg install chromium "
    "(нужно ≈300–400 МБ); Debian/Ubuntu — sudo apt install chromium; или укажите "
    "путь в LOGOS_BROWSER_CHROME (core/.env) и перезапустите Logos. "
    "Без Chrome работает движок http"
)

# Addresses and schemes
E_URL_SCHEME = Message(
    "Адрес должен начинаться с http://, https://, file:// или data: "
    "(например https://example.com)"
)
E_URL_SCHEME_HTTP = Message(
    "Поддерживаются только адреса http:// и https:// (получено: {scheme}:)"
)
E_NO_HOST = Message("В адресе нет имени сервера: {url}")
E_REDIRECT_LINK_LOCAL = Message(
    "Перенаправление на адрес link-local заблокировано ({url}). "
    + LINK_LOCAL_MESSAGE
)
E_GUARD_FAILED = Message("Не удалось проверить адрес {url} ({type}); запрос не выполнен")
E_GUARD_SETUP = Message("Не удалось включить проверку адресов в Chrome; вкладка закрыта")

# Redirects and network
E_TOO_MANY_REDIRECTS = Message(
    "Превышено max_redirects={n}; последний адрес: {url}. "
    "Увеличьте max_redirects или укажите 0"
)
E_REDIRECT_CYCLE = Message("Цикл перенаправлений: {url} (после {n} переходов)")
E_DNS = Message("Не удалось найти сервер {host}: проверьте адрес (ошибка DNS)")
E_CONNECT = Message("Не удалось подключиться к {host}: {reason}")
E_TIMEOUT = Message(
    "Превышено время ожидания {seconds:g} с ({what}). "
    "Увеличьте timeout_seconds или укажите 0 (без ограничения)"
)
E_TLS = Message("Ошибка TLS-сертификата у {host}: {reason}")
E_PROXY = Message("Ошибка прокси {proxy}: {reason}")
E_SOCKS = Message(
    "socks-прокси не поддерживается движком http (нет пакета PySocks); "
    "используйте http(s)-прокси"
)
E_BROKEN_READ = Message(
    "Соединение оборвалось при чтении ответа {url} (получено {n} байт)"
)
E_BAD_REQUEST = Message("Некорректный запрос: {reason}")
E_BODY_CUT = Message(
    "Загружено {read} из {total} байт: достигнут max_bytes={n}; остальное не "
    "загружено — увеличьте max_bytes или укажите 0"
)

# Tabs and sessions
E_NO_TABS = Message("Во вкладках ничего не открыто: начните с browser_open(url=…)")
E_TAB_EMPTY = Message(
    "Во вкладке {tab} ещё ничего не открыто: browser_open(url=…, tab='{tab}')"
)
E_TAB_NOT_FOUND = Message("Вкладка «{id}» не найдена. Открыты: {ids}")
E_TAB_LOST = Message(
    "Вкладка {tab} потеряна ({reason}). Откройте адрес заново: "
    "browser_open(url='{url}', tab='{tab}')"
)
E_SESSION_CLOSED = Message("Браузерная сессия {key} закрыта; откройте страницу заново")
E_SESSION_NOT_FOUND = Message(
    "Сессия «{id}» не найдена; список: browser_session(action='list')"
)
E_ENGINE_UNKNOWN = Message(
    "Движок «{engine}» не поддерживается: укажите http, chrome или auto"
)

# Element targets
E_REF_NOT_FOUND = Message(
    "Метка {ref} не найдена на странице {url}. Актуальные метки: "
    "browser_read(view='interactive'); после перехода метки новые"
)
E_TARGET_REQUIRED = Message("Укажите цель: ref (например L5), text или selector")
E_TEXT_NOT_FOUND = Message(
    "Элемент с текстом «{text}» не найден. Посмотрите метки: "
    "browser_read(view='interactive', filter='{text}')"
)
E_TEXT_AMBIGUOUS = Message(
    "Текст «{text}» есть у нескольких элементов: {list}. Укажите ref или nth"
)
E_SELECTOR_NONE = Message("По селектору «{sel}» ничего не найдено")
E_SELECTOR_SYNTAX = Message(
    "Не удалось разобрать CSS-селектор «{sel}»: {reason} (позиция {pos})"
)
E_NTH_RANGE = Message(
    "nth={nth}, а по цели {target} найдено элементов: {count}. Укажите nth от 1 до {count}"
)

# HTTP-engine limits
E_JS_NEEDED_HTTP = Message(
    "Элемент {ref} («{name}») работает через JavaScript, а вкладка {tab} открыта "
    "движком http. Откройте страницу в chrome: browser_open(url='{url}', "
    "tab='{tab}', engine='chrome') — и повторите"
)
E_CLICK_FORM = Message(
    "{ref} — форма; нажмите её кнопку отправки или используйте "
    "browser_fill_form(form='{ref}', submit=true)"
)
E_PRESS_NO_FORM = Message("Поле {ref} не принадлежит форме — отправлять нечего")
E_WAIT_HTTP = Message(
    "Во вкладке движка http можно ждать только seconds; для ожидания элементов и "
    "текста откройте страницу в chrome"
)

# Chrome
E_WEBSOCKETS = Message(
    "Для движка chrome нужен Python-пакет websockets 13 или новее: "
    "pip install 'websockets>=13'"
)
E_CHROME_ENV = Message(
    "LOGOS_BROWSER_CHROME указывает на «{value}», но это не исполняемый файл"
)
E_CHROME_START = Message(
    "Chrome не запустился ({binary}): код {code}. Журнал: {log_tail} "
    "(полный журнал: {path})"
)
E_CHROME_DIED = Message(
    "Chrome завершился неожиданно ({exit}); вкладки chrome потеряны. Откройте "
    "страницы заново — Chrome запустится снова"
)
E_CHROME_NAV = Message("Chrome не смог открыть {url}: {errorText}")
E_COVERED = Message(
    "Элемент {ref} перекрыт элементом {cover}. Закройте его (например "
    "browser_click(ref='{cover_ref}')) или повторите с force=true"
)
E_NOT_VISIBLE = Message(
    "Элемент {ref} не виден (скрыт или нулевого размера); force=true нажмёт его "
    "через JavaScript"
)
E_UNKNOWN_KEY = Message(
    "Неизвестная клавиша «{k}». Примеры: Enter, Tab, Escape, ArrowDown, Control+A"
)
E_WAIT_TIMEOUT = Message("Не дождались за {t} с: {conditions}")
E_EVAL = Message("Ошибка JavaScript: {text}")
E_EVAL_REQUIRED = Message("Укажите expression или result_id")
E_RESULT_ID = Message("Результат «{id}» не найден; сохранены: {ids}")
E_BODY_GONE = Message("Тело ответа {id} уже недоступно в Chrome")
E_BLOB_LINK = Message(
    "Ссылка ведёт на blob:-адрес внутри страницы; нажмите её в chrome "
    "(browser_click) — файл попадёт в папку загрузок сессии {dir}"
)

# Forms. E_FIELD_NOT_FOUND: {where} is " в форме F1" or empty.
E_FIELD_NOT_FOUND = Message("Поле «{field}» не найдено{where}. Поля: {list}")
E_OPTION_NOT_FOUND = Message("В списке {ref} нет варианта «{value}». Варианты: {options}")
E_FORM_MIXED = Message(
    "Поля относятся к разным формам ({forms}); заполняйте по одной форме"
)
E_FORM_DIALOG = Message(
    "Форма закрывает диалог страницы (method=dialog) и ничего не отправляет"
)
E_FORM_SCHEME = Message(
    "Форма отправляется не по HTTP ({scheme}:); такую отправку выполнить нельзя"
)

# Files and disk. E_DISK_FULL: {need_part} is ", нужно N" or empty.
E_PATH_REQUIRED = Message("Укажите path — файл или папку для сохранения")
E_FILE_EXISTS = Message(
    "Файл уже существует: {path}. Передайте overwrite=true или другой path"
)
E_PATH_IS_DIR = Message(
    "По пути {path} находится папка; укажите имя файла или путь с / на конце"
)
E_DISK_FULL = Message(
    "Недостаточно места на диске для {path}: свободно {free}{need_part}. "
    "Запись отменена, временный файл удалён"
)
E_MAX_BYTES = Message(
    "Файл больше max_bytes={n} (получено {got}); загрузка прервана, частичный "
    "файл удалён"
)
E_DOWNLOAD_STATUS = Message("Сервер ответил {status} {reason} на {url}; файл не сохранён")
E_FILE_MISSING = Message("Файл не найден: {path}")

# Views and reading. E_HISTORY_BACK: {earlier} is "; раньше: …" or empty.
E_BINARY_VIEW = Message(
    "Документ во вкладке {tab} — файл ({mime}, {size}); доступны view='content' и "
    "'headers'. Скачайте: browser_download(tab='{tab}', path='…', overwrite=false)"
)
E_VIEW_PARAM = Message("Параметр {param} не применим к view='{view}'")
E_SECTION = Message(
    "Раздел «{section}» не найден. Оглавление: browser_read(view='outline')"
)
E_SECTION_AMBIGUOUS = Message(
    "Раздел «{section}» подходит к нескольким заголовкам: {list}. Укажите номер раздела"
)
E_OFFSET = Message("offset={offset} больше длины текста ({total} симв.)")
E_NOT_JSON = Message("Содержимое не является JSON (Content-Type: {ct}): {reason}")
E_JSON_PATH = Message("json_path «{path}»: {reason}")
E_REGEX = Message("Некорректное регулярное выражение «{q}»: {reason}")
E_HISTORY_BACK = Message(
    "Назад некуда: это первая страница вкладки {tab} в движке {engine}{earlier}"
)
E_HISTORY_FWD = Message("Вперёд некуда: это последняя страница вкладки {tab}")
E_CAPTURE_FORMAT = Message(
    "Не удалось определить формат: укажите format (png, jpeg, pdf) или "
    "расширение .png, .jpg, .pdf"
)

# Requests and cookies
E_RESPONSE_ID = Message("Ответ «{id}» не найден; сохранены: {ids}")
E_REQUEST_URL = Message("Укажите url или response_id")
E_BODY_CONFLICT = Message(
    "Укажите только одно тело запроса: json_body, form (с files), body или body_base64"
)
E_JSON_BODY = Message("json_body не является корректным JSON: {reason}")
# Formats to "... (подстановка {{cookie:NAME}})".
E_COOKIE_PLACEHOLDER = Message(
    "Cookie «{name}» для {host} не найдена (подстановка {{{{cookie:{name}}}}})"
)
E_COOKIE_FIELDS = Message("Для set нужны name, value и domain или url")

# Search. The two texts below show literal {query}/{offset}: they take no fields.
E_SEARCH_BLOCKED = Message(
    "{engine} показал проверку «я не робот» и не отдал результаты. Попробуйте "
    "позже, другой engine или откройте поиск в chrome"
)
E_SEARCH_CUSTOM = _Literal(
    "Для engine='custom' нужны url_template с {query} и result_selector"
)
E_SEARCH_PAGE_SIZE = _Literal("Для {offset} в url_template нужен page_size")

# Approval: the call is refused until the caller approves it.
E_APPROVAL_REQUIRED = Message(
    "Для «{tool}» нужно подтверждение владельца: без него действие не выполнено"
)

# Argument validation
E_ARG_TYPE = Message("Параметр {name} должен быть {expected} (получено: {value})")
E_ARG_NEGATIVE = Message("Параметр {name} не может быть меньше 0 (получено: {value})")


def _problem_clause(problem: str | None) -> str:
    """A problem text that reads well after "но": lowercase start, no final period."""
    text = single_line(problem or "").strip().rstrip(".")
    if not text:
        return "Chrome/Chromium не найден"
    if len(text) > 1 and text[0].isupper() and text[1].islower():
        text = text[0].lower() + text[1:]
    return text


def chrome_missing_message(action: str, problem: str | None) -> str:
    """The action needs Chrome, which is missing or unusable; how to install it."""
    return (
        f"Действие «{action}» требует движок chrome, но {_problem_clause(problem)}. "
        + CHROME_INSTALL_HINT
    )


def chrome_only_message(action: str, tab: str, url: str) -> str:
    """The tab runs on the http engine and the action needs Chrome."""
    return (
        f"Вкладка {tab} открыта движком http, а «{action}» выполняется только в "
        f"chrome: browser_open(url='{url}', tab='{tab}', engine='chrome'), "
        "затем повторите"
    )


def auto_no_chrome_message(reason: str, problem: str | None) -> str:
    """Note for auto mode: the page needs JavaScript but Chrome is unavailable."""
    return (
        f"Страница, похоже, собирается JavaScript ({reason}), а "
        f"{_problem_clause(problem)} — текст может быть неполным. "
        + CHROME_INSTALL_HINT
    )


# E_AUTO_NO_CHROME as a template: fields reason and problem.
E_AUTO_NO_CHROME = Message(
    "Страница, похоже, собирается JavaScript ({reason}), а {problem} — текст может "
    "быть неполным. " + CHROME_INSTALL_HINT
)


def timeout_message(seconds: float | None, what: str) -> str:
    """E_TIMEOUT for a deadline of ``seconds``."""
    return E_TIMEOUT.format(seconds=float(seconds or 0), what=what)


def disk_full_message(path: str, free: str, need: str | None = None) -> str:
    """E_DISK_FULL with the optional ", нужно N" part."""
    return E_DISK_FULL.format(
        path=path, free=free, need_part=f", нужно {need}" if need else ""
    )


def history_back_message(tab: str, engine: str, earlier: str | None = None) -> str:
    """E_HISTORY_BACK with the optional "; раньше: …" part."""
    return E_HISTORY_BACK.format(
        tab=tab, engine=engine, earlier=f"; раньше: {earlier}" if earlier else ""
    )


def field_not_found_message(field: str, form_ref: str | None, fields: str) -> str:
    """E_FIELD_NOT_FOUND with the optional " в форме F1" part."""
    return E_FIELD_NOT_FOUND.format(
        field=field, where=f" в форме {form_ref}" if form_ref else "", list=fields
    )


# ---------------------------------------------------------------------------
# Deadlines


class Deadline:
    """A whole-operation time budget; None or a non-positive value means none."""

    __slots__ = ("seconds", "_end")

    def __init__(self, seconds: float | None) -> None:
        value: float | None
        try:
            value = None if seconds is None else float(seconds)
        except (TypeError, ValueError):
            value = None
        if value is None or not math.isfinite(value) or value <= 0:
            self.seconds: float | None = None
            self._end: float | None = None
        else:
            self.seconds = value
            self._end = time.monotonic() + value

    def remaining(self) -> float | None:
        """Seconds left (never negative), or None without a deadline."""
        if self._end is None:
            return None
        return max(0.0, self._end - time.monotonic())

    def expired(self) -> bool:
        return self._end is not None and time.monotonic() >= self._end

    def check(self, what: str) -> None:
        """Raise BrowserTimeout(E_TIMEOUT) when the budget is spent."""
        if self.expired():
            raise BrowserTimeout(timeout_message(self.seconds, what))

    def __repr__(self) -> str:
        return f"Deadline({self.seconds!r})"


__all__ = [
    "BrowserBlocked",
    "BrowserError",
    "BrowserNetworkError",
    "BrowserNotFound",
    "BrowserTimeout",
    "BrowserUnavailable",
    "CHROME_INSTALL_HINT",
    "Deadline",
    "LINK_LOCAL_MESSAGE",
    "Message",
    "auto_no_chrome_message",
    "chrome_missing_message",
    "chrome_only_message",
    "disk_full_message",
    "field_not_found_message",
    "history_back_message",
    "message",
    "single_line",
    "timeout_message",
]

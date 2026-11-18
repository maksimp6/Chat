"""The company's own shop on its site: offers, demo, order page, download.

Owner, 18.11.2026: "so work until business is running"; "we can cheat to have unlimited
powerful smart workers with data so we must use this advantage to for our company". The
shop is the company's keyless channel ``site``: a visitor opens ``/shop/`` on the shop
name, tries a product's demo, orders it and gets the invoice on the order page at once;
when the owner has confirmed the payment, the same page gives the files.

What the shop keeps to:

- it reads everything from the ledger and writes only fixed texts into it: a buyer types
  no free text (an order is an offer and an optional contact of one of three fixed
  shapes; a follow-up is one of two fixed signals);
- it answers and bills by itself, in the request that creates the order (actor
  ``channels``, behind the Court gate of ``corporation/channels.py``);
- an order is opened only by its secret, which is in the page's address and nowhere in
  the ledger (the ledger keeps its SHA-256);
- a product says what its demo serves and what its archive holds in ``logos-shop.json``
  next to it; the shop serves exactly the listed files and knows no product by name;
- it answers on its own name only (``host_rule``), its pages carry no script, and no
  view calls a model, the runner or the cycle.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import tempfile
import threading
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from corporation import channels
from corporation import ledger as journal
from webframe import (
    Blueprint,
    HTTPError,
    MethodNotAllowed,
    NotFound,
    Response,
    redirect,
    render_template,
    request,
    send_file,
)

logger = logging.getLogger("logos.corporation.shop")

__all__ = [
    "SHOP_HOST",
    "SHOP_PREFIX",
    "descriptor_report",
    "host_rule",
    "shop_bp",
    "shop_headers",
]

SHOP_HOST = "www.maxxxpavlov.ru"  # the same name as home_server.SHOP_HOST
SHOP_PREFIX = "/shop/"

CHANNEL = "site"
DESCRIPTOR = "logos-shop.json"
# 64: the channels tool's bound for a short string
CONTACT_MAX = channels._ID["maxLength"]
# 4000 bytes: the bound every channel puts on a text; a valid form is under 400 bytes
# (the same number as home_server.SHOP_BODY_MAX)
BODY_MAX = channels._TEXT["maxLength"]
_TOKEN = re.compile(r"[A-Za-z0-9_-]{32}")  # secrets.token_urlsafe(24)
_OFFER = re.compile(r"o-[0-9]{4,8}")
_PRODUCT = re.compile(r"pr-[0-9]{4,8}")
_EMAIL = re.compile(
    r"[A-Za-z0-9._%+-]{1,40}@[A-Za-z0-9-]{1,40}(\.[A-Za-z0-9-]{1,20}){1,3}"
)
_PHONE = re.compile(r"\+?[0-9]{10,15}")
_TELEGRAM = re.compile(r"@[A-Za-z][A-Za-z0-9_]{4,31}")
_ARCHIVE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
ACK_ORDER = "Заказ принят, счёт выставлен. Оплатите его по реквизитам на этой странице и нажмите «Я оплатил(а)»."
# the answer to an order taken while the owner has not set the payment text
ACK_ORDER_WAIT = "Заказ принят, счёт выставлен. Реквизиты для оплаты продавец разместит на этой странице — сохраните её адрес."
SIGNALS = {  # signal -> (what the buyer's record says, what the shop answers)
    "paid": (
        "Покупатель сообщает об оплате.",
        "Спасибо! Продавец проверит поступление оплаты. После подтверждения на этой странице появится ссылка для скачивания.",
    ),
    "help": (
        "Покупатель просит продавца связаться с ним.",
        "Сообщение передано продавцу. Ответ появится на этой странице; если вы оставили контакт, продавец может написать и туда.",
    ),
}
PAGE_CSP = (
    "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
    "base-uri 'none'; frame-ancestors 'none'"
)
# Every demo file is sent with this policy. A product's descriptor may add a policy of
# its own (``demo.csp``): it is sent beside this one, and a browser keeps to both.
DEMO_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
    "font-src 'self'; connect-src 'self'; manifest-src 'self'; worker-src 'none'; base-uri 'none'; form-action 'none'"
)
NO_INDEX = "noindex, nofollow"
# The longest header line ``http.client`` reads (its ``_MAXLINE``): the home proxy reads
# the shop's answers with it.
_HEADER_LINE_MAX = 65536
_DESCRIPTOR_KEYS = ("version", "product", "archive", "demo")
_ARCHIVE_KEYS = ("name", "files")
_DEMO_KEYS = ("entry", "files", "overrides", "withheld", "note", "csp")
# The phone's ``mimetypes`` table is unknown; the demo's types are named here.
_TYPES = {
    ".html": "text/html",
    ".css": "text/css",
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".json": "application/json",
    ".webmanifest": "application/manifest+json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".ico": "image/x-icon",
    ".txt": "text/plain",
    ".woff2": "font/woff2",
    ".wasm": "application/wasm",
}
# code -> (status, heading, text) of a page that only says something
_MESSAGES = {
    "not_found": (
        404,
        "Страница не найдена",
        "Такой страницы в магазине нет. Проверьте адрес или вернитесь на витрину.",
    ),
    "no_order": (
        404,
        "Заказ не найден",
        "Проверьте адрес страницы заказа: он должен быть скопирован целиком.",
    ),
    "no_offer": (
        404,
        "Предложение не продаётся",
        "Это предложение сейчас не продаётся. Вернитесь на витрину.",
    ),
    "not_paid": (
        403,
        "Оплата не подтверждена",
        "Ссылка для скачивания появится на странице заказа, когда продавец подтвердит оплату.",
    ),
    "closed": (503, "Магазин временно закрыт", "Попробуйте позже."),
    "no_delivery": (
        503,
        "Магазин временно закрыт",
        "Выдача файлов временно приостановлена. Попробуйте позже или нажмите на странице заказа «Нужна помощь продавца».",
    ),
    "paused": (
        503,
        "Магазин временно закрыт",
        "Выдача файлов временно приостановлена. Попробуйте позже.",
    ),
    "bad_form": (
        400,
        "Запрос не принят",
        "Форма отправлена неверно. Вернитесь назад и попробуйте ещё раз.",
    ),
    "failed": (500, "Что-то пошло не так", "Попробуйте ещё раз чуть позже."),
}
BAD_CONTACT = "Контакт указан неверно. Напишите e-mail латиницей, телефон цифрами или имя в Telegram с @ — или оставьте поле пустым."
_MOSCOW = timezone(timedelta(hours=3))
_DELIVERY_LOCK = threading.Lock()
_SIGNAL_LOCK = threading.Lock()
# guards the three caches below
_CACHE_LOCK = threading.Lock()
# descriptor file -> ((st_mtime_ns, st_size), its JSON value or None, what is wrong)
_DESCRIPTORS: dict[str, tuple[tuple[int, int], Any, str]] = {}
# what an ERROR line was last written about -> the state it was written for
_REPORTED: dict[str, Any] = {}
# (the ledger, the number of its records, the State built from them)
_STATE: tuple[Any, int, journal.State] | None = None
shop_bp = Blueprint("shop", __name__, url_prefix="/shop")


# ----- the shop's own name ------------------------------------------------------------------


def _named_hosts() -> set[str]:
    names: set[str] = set()
    for header in ("Host", "X-Forwarded-Host"):
        for part in (request.headers.get(header) or "").split(","):
            names.add(part.strip().rsplit(":", 1)[0].lower().rstrip("."))
    return names


def _shop_path() -> bool:
    return request.path == "/shop" or request.path.startswith(SHOP_PREFIX)


def host_rule() -> Response | None:
    """A request for the shop name sees only the shop, and the shop answers only there.
    A shop path no rule matches is answered with the shop's own page."""
    shop_path = _shop_path()
    if (SHOP_HOST in _named_hosts()) != shop_path:
        return NotFound().get_response()
    if shop_path and isinstance(
        request.routing_exception, (NotFound, MethodNotAllowed)
    ):
        return _refuse("not_found")
    return None


def shop_headers(response: Response) -> Response:
    """The headers of every answer the shop name gets under the shop's paths, whether a
    rule matched or not (an application-level hook, registered beside ``host_rule``).
    A header the view has set stays as it is."""
    if _shop_path() and SHOP_HOST in _named_hosts():
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Cache-Control", "no-store")
        if response.status_code >= 300 and response.status_code != 304:
            response.headers.setdefault("X-Robots-Tag", NO_INDEX)
    return response


# ----- what is read once and kept -----------------------------------------------------------


def _report(subject: str, mark: Any, message: str, *args: Any) -> None:
    """An ERROR line, written once per change of what it is about, not per request."""
    with _CACHE_LOCK:
        if _REPORTED.get(subject) == mark:
            return
        _REPORTED[subject] = mark
    logger.error(message, *args)


def _settled(subject: str) -> None:
    """What ``_report`` wrote about is in order again."""
    with _CACHE_LOCK:
        _REPORTED.pop(subject, None)


def _state() -> journal.State:
    """The ledger's state for a shop view: built once per record of the ledger and kept
    until the next one. The views only read it, and it is handed to nothing that
    changes a state."""
    global _STATE
    book = journal.ledger()
    entries = book.entries()
    with _CACHE_LOCK:
        kept = _STATE
    if kept is not None and kept[0] is book and kept[1] == len(entries):
        return kept[2]
    built = journal.build_state(entries)
    with _CACHE_LOCK:
        _STATE = (book, len(entries), built)
    return built


# ----- the product's shop descriptor --------------------------------------------------------


@dataclass(frozen=True)
class Showcase:
    """A product's valid ``logos-shop.json``: what its demo serves, what its archive
    holds."""

    root: Path
    archive_name: str
    archive_files: tuple[str, ...]
    demo_entry: str  # "" when the product has no demo
    demo_files: frozenset[str]
    demo_overrides: dict[str, str]
    demo_note: str
    demo_csp: str  # the product's own policy, sent beside DEMO_CSP; "" when it has none


def _control(text: str) -> bool:
    """Whether the text has a control character."""
    return any(ord(ch) < 32 or 127 <= ord(ch) < 160 for ch in text)


def _inside(root: Path, name: Any) -> Path | None:
    """The file a descriptor's path names under the product root, as a real path, or
    None: a POSIX relative path with no empty, ``.`` or ``..`` segment whose real path
    is a file under the root."""
    if not isinstance(name, str) or not name or name.startswith("/"):
        return None
    if "\\" in name or ":" in name or _control(name):
        return None
    if any(part in ("", ".", "..") for part in name.split("/")):
        return None
    try:
        path = (root / name).resolve()
        path.relative_to(root.resolve())
        return path if path.is_file() else None
    except (OSError, RuntimeError, ValueError):
        return None


def _placed(product: dict[str, Any]) -> tuple[Path, Path] | None:
    """(project folder, registered file) as real paths, or None when the registered
    file is not a file really inside the project's folder: such a product is not sold."""
    from corporation.tools import _project_dir

    raw = str(product.get("path") or "")
    subject = f"placed {product.get('id')}"
    found: tuple[Path, Path] | None = None
    if raw:  # an empty path would resolve to the working directory
        try:
            top = _project_dir(str(product.get("project") or "")).resolve()
            registered = Path(raw).resolve()
            registered.relative_to(top)
            if registered.is_file():
                found = (top, registered)
        except (OSError, RuntimeError, ValueError):
            pass
    if found is None:
        _report(
            subject,
            raw,
            "[SHOP] product %s is not sold: its registered file is not a file inside its project folder",
            product.get("id"),
        )
    else:
        _settled(subject)
    return found


def _descriptor_path(top: Path, registered: Path) -> Path | None:
    """The nearest descriptor in the real folder of the registered file or in a parent
    of it, up to and including the project's real folder."""
    folder = registered.parent
    for candidate in (folder, *folder.parents):
        if (candidate / DESCRIPTOR).is_file():
            return candidate / DESCRIPTOR
        if candidate == top:
            break
    return None


def _descriptor(path: Path) -> tuple[Any, str]:
    """(the descriptor's JSON value, "") or (None, what is wrong with the file). The
    file is read and parsed once per change of it (its time and size)."""
    try:
        stat = path.stat()
    except OSError as exc:
        return None, f"{DESCRIPTOR} is not readable: {type(exc).__name__}"
    mark = (stat.st_mtime_ns, stat.st_size)
    with _CACHE_LOCK:
        kept = _DESCRIPTORS.get(str(path))
    if kept is not None and kept[0] == mark:
        return kept[1], kept[2]
    try:
        # utf-8-sig: a byte-order mark at the start is not part of the text
        value, problem = json.loads(path.read_text(encoding="utf-8-sig")), ""
    except (OSError, ValueError, RuntimeError) as exc:
        value, problem = (
            None,
            f"{DESCRIPTOR} is not readable JSON: {type(exc).__name__}",
        )
    with _CACHE_LOCK:
        _DESCRIPTORS[str(path)] = (mark, value, problem)
    return value, problem


def _names(value: Any, label: str, problems: list[str]) -> list[str]:
    """A descriptor's list of names: strings, each once."""
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        problems.append(f"{label} must be a list of file names")
        return []
    if len(set(value)) != len(value):
        problems.append(f"{label} names a file twice")
    return list(value)


def _unknown(given: dict[str, Any], known: tuple[str, ...], label: str) -> list[str]:
    """A problem for every key the descriptor's object has and the shop does not read."""
    return [f"{label}unknown key {key}" for key in given if key not in known]


def _showcase(
    product: dict[str, Any], placed: tuple[Path, Path] | None = None
) -> tuple[Showcase | None, list[str]]:
    """(descriptor, problems): (None, []) when the product has none, (None, [...]) when
    it is broken. It never raises: whatever fails while the descriptor is read, parsed
    or its paths are resolved is a problem of this product alone. ``placed`` is the
    product's ``_placed()`` when the caller has it."""
    try:
        showcase, problems = _read_showcase(product, placed or _placed(product))
    except (OSError, ValueError, RuntimeError) as exc:
        showcase, problems = (
            None,
            [f"{DESCRIPTOR} could not be read: {type(exc).__name__}"],
        )
    subject = f"descriptor {product.get('id')}"
    if problems:
        _report(
            subject,
            tuple(problems),
            "[SHOP] the descriptor of %s is broken: %s",
            product.get("id"),
            "; ".join(problems),
        )
    else:
        _settled(subject)
    return showcase, problems


def _read_showcase(
    product: dict[str, Any], placed: tuple[Path, Path] | None
) -> tuple[Showcase | None, list[str]]:
    if placed is None:
        return None, []
    path = _descriptor_path(*placed)
    if path is None:
        return None, []
    root = path.parent
    raw, unreadable = _descriptor(path)
    if unreadable:
        return None, [unreadable]
    if not isinstance(raw, dict):
        return None, [f"{DESCRIPTOR} must be an object"]
    problems = _unknown(raw, _DESCRIPTOR_KEYS, "")
    version = raw.get("version")
    if type(version) is not int or version != 1:
        problems.append("version must be 1")
    if "product" in raw and raw["product"] != product.get("id"):
        problems.append(f"product names another product, not {product.get('id')}")

    entry, note, csp = "", "", ""
    demo_files: list[str] = []
    overrides: dict[str, str] = {}
    demo = raw.get("demo")
    if "demo" in raw and not isinstance(demo, dict):
        problems.append("demo must be an object")
    elif demo is not None:
        problems.extend(_unknown(demo, _DEMO_KEYS, "demo: "))
        demo_files = _names(demo.get("files"), "demo.files", problems)
        given = demo.get("overrides", {})
        if not isinstance(given, dict):
            problems.append("demo.overrides must be an object {served name: source}")
            given = {}
        for name, source in given.items():
            if name not in demo_files:
                problems.append(f"demo.overrides: {name} is not in demo.files")
            if _inside(root, source) is None:
                problems.append(
                    f"demo.overrides: the source of {name} is not a file under the product root"
                )
            else:
                overrides[name] = source
        for name in demo_files:
            if name not in given and _inside(root, name) is None:
                problems.append(
                    f"demo.files: {name} is not a file under the product root"
                )
        entry = demo.get("entry")
        if not isinstance(entry, str) or entry not in demo_files:
            problems.append("demo.entry must be one of demo.files")
            entry = ""
        withheld = demo.get("withheld", [])
        if not isinstance(withheld, list) or not all(
            isinstance(name, str) for name in withheld
        ):
            problems.append("demo.withheld must be a list of file names")
        elif any(name in demo_files for name in withheld):
            problems.append("demo.withheld names a file that demo.files serves")
        note = demo.get("note")
        if not isinstance(note, str) or not note.strip() or _control(note):
            problems.append(
                "demo.note must be one paragraph of text without control characters"
            )
            note = ""
        if "csp" in demo:
            csp = demo["csp"]
            # it is sent as one header line beside DEMO_CSP
            if (
                not isinstance(csp, str)
                or not csp.strip()
                or not all(" " <= ch <= "~" for ch in csp)
            ):
                problems.append(
                    "demo.csp must be printable ASCII text on one line (or absent)"
                )
                csp = ""
            elif (
                len("Content-Security-Policy: ") + len(csp) + len("\r\n")
                > _HEADER_LINE_MAX
            ):
                problems.append("demo.csp does not fit into one header line")
                csp = ""
            elif "report-uri" in csp.lower() or "report-to" in csp.lower():
                problems.append("demo.csp must not have report-uri or report-to")
                csp = ""

    archive_name = ""
    archive_files: list[str] = []
    archive = raw.get("archive")
    if not isinstance(archive, dict):
        problems.append("archive must be an object")
    else:
        problems.extend(_unknown(archive, _ARCHIVE_KEYS, "archive: "))
        archive_name = archive.get("name")
        if (
            not isinstance(archive_name, str)
            or not _ARCHIVE.fullmatch(archive_name)
            or archive_name.endswith(".")
        ):
            problems.append(
                "archive.name must be letters, digits, dots, dashes and underscores, not ending in a dot"
            )
            archive_name = ""
        archive_files = _names(archive.get("files"), "archive.files", problems)
        if not archive_files:
            problems.append("archive.files must name at least one file")
        if len({name.casefold() for name in archive_files}) != len(
            set(archive_files)
        ):
            problems.append(
                "archive.files names two files that differ only in letter case"
            )
        for name in archive_files:
            if name == DESCRIPTOR or name in overrides.values():
                problems.append(
                    f"archive.files: {name} is the descriptor or a demo override source"
                )
            elif _inside(root, name) is None:
                problems.append(
                    f"archive.files: {name} is not a file under the product root"
                )
    if problems:
        return None, problems
    return (
        Showcase(
            root=root,
            archive_name=archive_name,
            archive_files=tuple(archive_files),
            demo_entry=entry,
            demo_files=frozenset(demo_files),
            demo_overrides=overrides,
            demo_note=note,
            demo_csp=csp,
        ),
        [],
    )


def descriptor_report(product: dict[str, Any]) -> dict[str, Any]:
    """{"present", "ok", "errors", "demo", "archive_files"}: how the shop reads the
    product's descriptor, for the next step's worker."""
    showcase, problems = _showcase(product)
    return {
        "present": showcase is not None or bool(problems),
        "ok": showcase is not None,
        "errors": problems,
        "demo": bool(showcase is not None and showcase.demo_entry),
        "archive_files": len(showcase.archive_files) if showcase is not None else 0,
    }


# ----- what is on sale ------------------------------------------------------------------------


def _shelf(st: journal.State) -> list[dict[str, Any]]:
    """The offers on sale, newest first: published on the site, of a product the critic
    passed whose registered file is a file inside its project folder. Empty while the
    channel is off."""
    if not st.channels.get(CHANNEL, {}).get("enabled"):
        return []
    items: list[dict[str, Any]] = []
    for offer in st.offers.values():
        if offer["status"] != "published" or offer["channel"] != CHANNEL:
            continue
        product = st.products.get(offer["product"] or "")
        if product is None or product["status"] != "ready":
            continue
        placed = _placed(product)
        if placed is None:
            continue
        showcase, problems = _showcase(product, placed)
        items.append(
            {
                "offer": offer,
                "product": product,
                "showcase": showcase,
                "problems": problems,
            }
        )
    items.sort(
        key=lambda item: (
            item["offer"].get("published") or item["offer"]["at"],
            item["offer"]["id"],
        ),
        reverse=True,
    )
    return items


def _on_sale(st: journal.State, offer: Any) -> dict[str, Any] | None:
    """The shelf's item of the offer with this id."""
    if not isinstance(offer, str) or not _OFFER.fullmatch(offer):
        return None
    return next((i for i in _shelf(st) if i["offer"]["id"] == offer), None)


# ----- pages ----------------------------------------------------------------------------------


def _page(view: str, status: int = 200, **context: Any) -> Response:
    """One of the shop's pages (``templates/shop.html``, switched by ``view``)."""
    response = Response(
        render_template("shop.html", view=view, **context), status=status
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = PAGE_CSP
    if view in ("order", "message") or status != 200:
        response.headers["X-Robots-Tag"] = NO_INDEX
    return response


def _message(code: str, status: int | None = None) -> Response:
    """A page that only says something: an error of the table above."""
    default, title, text = _MESSAGES[code]
    return _page("message", status or default, title=title, text=text)


def _refuse(code: str) -> Response:
    logger.warning("[SHOP] refused %s", code)
    return _message(code)


def _price(amount: Any, currency: Any) -> str:
    """4900.0, "RUB" -> «4 900 ₽» (the spaces do not break)."""
    digits = f"{float(amount):,.0f}".replace(",", " ")
    return f"{digits} {'₽' if currency == 'RUB' else currency}"


def _when(at: Any) -> str:
    """A record's time as ДД.ММ.ГГГГ ЧЧ:ММ, Moscow time."""
    try:
        moment = datetime.fromisoformat(str(at).replace("Z", "+00:00"))
    except ValueError:
        return str(at or "")
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(_MOSCOW).strftime("%d.%m.%Y %H:%M")


def _files_word(count: int) -> str:
    if count % 10 == 1 and count % 100 != 11:
        return "файл"
    if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        return "файла"
    return "файлов"


def _demo_href(item: dict[str, Any]) -> str:
    showcase = item["showcase"]
    if showcase is None or not showcase.demo_entry:
        return ""
    return f"/shop/demo/{item['product']['id']}/"


def _offer_page(
    item: dict[str, Any], status: int = 200, error: str = "", contact: str = ""
) -> Response:
    offer, product, showcase = item["offer"], item["product"], item["showcase"]
    after = "скачивается со страницы заказа после подтверждения оплаты."
    if showcase is not None:
        count = len(showcase.archive_files)
        receive = (
            f"Архив с файлами продукта ({count} {_files_word(count)}). Он {after}"
        )
    elif item["problems"]:
        receive = f"Архив с файлами продукта. Он {after}"
    else:
        receive = f"Файл продукта. Он {after}"
    return _page(
        "offer",
        status,
        title=product["title"] or offer["id"],
        offer=offer["id"],
        text=offer["text"],
        price=_price(offer["price"], offer["currency"]),
        demo_href=_demo_href(item),
        demo_note=showcase.demo_note if showcase is not None else "",
        receive=receive,
        error=error,
        contact=contact,
        contact_max=CONTACT_MAX,
    )


def _to_order(token: str) -> Response:
    """303 to the order's page; the address carries the secret, so nothing keeps it."""
    response = redirect(f"/shop/order?token={token}", code=303)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Robots-Tag"] = NO_INDEX
    return response


def _private(response: Response) -> Response:
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Robots-Tag"] = NO_INDEX
    return response


@shop_bp.errorhandler(Exception)
def _error(error: Exception) -> Response:
    """The shop's own page for whatever went wrong; never the error's text, a path or a
    traceback."""
    if isinstance(error, HTTPError):
        status = error.code or 500
        # any other status keeps its number and shows the texts of a request not taken
        code = {404: "not_found", 500: "failed", 503: "closed"}.get(status, "bad_form")
        logger.warning("[SHOP] refused %s", status)
        return _message(code, status)
    logger.error("[SHOP] request failed", exc_info=error)
    return _message("failed")


# ----- the shelf and the product page ---------------------------------------------------------


@shop_bp.route("/")
def shelf() -> Response:
    st = _state()
    cards = [
        {
            "title": item["product"]["title"] or item["offer"]["id"],
            "lead": item["offer"]["text"].strip().split("\n\n")[0],
            "price": _price(item["offer"]["price"], item["offer"]["currency"]),
            "href": f"/shop/offer/{item['offer']['id']}",
            "demo_href": _demo_href(item),
        }
        for item in _shelf(st)
    ]
    return _page("shelf", title="Магазин приложений", cards=cards)


@shop_bp.route("/offer/<offer>")
def offer_page(offer: str) -> Response:
    # the id's shape is looked at before the ledger is
    item = _on_sale(_state(), offer) if _OFFER.fullmatch(offer) else None
    if item is None:
        return _refuse("no_offer")
    return _offer_page(item)


# ----- the order ------------------------------------------------------------------------------


def _form() -> Any:
    """The request's form (a MultiDict), or None when it is not the shop's own form."""
    if request.mimetype != "application/x-www-form-urlencoded":
        return None
    if (request.headers.get("Sec-Fetch-Site") or "same-origin") not in (
        "same-origin",
        "none",
    ):
        # the form is posted from the shop's own page
        return None
    if (request.headers.get("Sec-Fetch-Mode") or "navigate") != "navigate":
        # a form a person submits is a navigation
        return None
    length = request.content_length
    if length is None or length > BODY_MAX:
        # decided on the header, before the body is read; a chunked body has none
        return None
    form = request.form
    if (form.get("website") or "").strip():
        return None  # a field no person sees
    return form


def _contact(value: Any) -> str | None:
    """"" when none was given, the contact in its fixed shape, or None when it has no
    such shape or one of the Court gate's own expressions matches it (the form and the
    gate then read a contact the same way)."""
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) > CONTACT_MAX or not text.isascii():
        return None
    phone = re.sub(r"[ ()-]", "", text)
    if _PHONE.fullmatch(phone):
        found = phone
    elif _EMAIL.fullmatch(text) or _TELEGRAM.fullmatch(text):
        found = text
    else:
        return None
    if any(pattern.search(found) for pattern in channels._SECRET_PATTERNS):
        return None
    return found


def _payment_text() -> str:
    """The owner's payment text as it is set now; "" while he has not set it."""
    return (os.environ.get("LOGOS_CORP_PAYMENT_TEXT") or "").strip()


def _order(
    st: journal.State, token: Any
) -> tuple[str, dict[str, Any], dict[str, Any]] | None:
    """(client id, client, order) of the order this secret opens, or None.

    The secret is never stored: the order keeps its SHA-256 in full and the client id is
    only the first 12 hex digits of it, so knowing a client id opens nothing."""
    if not isinstance(token, str) or not _TOKEN.fullmatch(token):
        return None
    digest = hashlib.sha256(token.encode("ascii")).hexdigest()
    cid = "site-" + digest[:12]
    client, order = st.clients.get(cid), st.orders.get(cid)
    if client is None or order is None or client.get("channel") != CHANNEL:
        return None
    if not hmac.compare_digest(
        str(order["key"]).encode("utf-8"), digest.encode("ascii")
    ):
        return None
    return cid, client, order


@shop_bp.route("/order", methods=["POST"])
def order_create() -> Response:
    """Take the order, answer and bill, all in this request; then show its page."""
    form = _form()
    if form is None:
        return _refuse("bad_form")
    oid = form.get("offer")
    # the id's shape is looked at before the ledger is
    if not isinstance(oid, str) or not _OFFER.fullmatch(oid):
        return _refuse("no_offer")
    st = _state()
    item = _on_sale(st, oid)
    if item is None:
        return _refuse("no_offer")
    offer, product = item["offer"], item["product"]
    contact = _contact(form.get("contact"))
    if contact is None:
        logger.warning("[SHOP] refused bad_contact")
        return _offer_page(
            item,
            400,
            error=BAD_CONTACT,
            contact=str(form.get("contact") or "")[:CONTACT_MAX],
        )
    try:
        # neither a client nor a text is passed: the only refusal is a channel that is off
        channels.gate(st, CHANNEL, "invoice")
    except channels.GateRefused:
        return _refuse("closed")
    for _ in range(4):
        token = secrets.token_urlsafe(24)
        digest = hashlib.sha256(token.encode("ascii")).hexdigest()
        cid = "site-" + digest[:12]
        if cid not in st.clients:
            break
    else:
        raise RuntimeError("no free order id")
    price = f"{offer['price']:.0f} {offer['currency']}"
    line = f"Заказ {oid}: «{product['title']}», {price}." + (
        f" Контакт: {contact}." if contact else ""
    )
    book = journal.ledger()
    with journal.embedding_deferred():
        book.append(
            "client.message",
            "channels",
            {
                "client": cid,
                "channel": CHANNEL,
                "signal": "order",
                "text": line,
                "key": digest,
                "contact": contact,
                "amount": offer["price"],
                "currency": offer["currency"],
            },
            offer=oid,
            product=offer["product"],
            project=offer["project"],
        )
        book.append(
            "client.reply",
            "channels",
            {
                "client": cid,
                "channel": CHANNEL,
                "text": ACK_ORDER if _payment_text() else ACK_ORDER_WAIT,
                "auto": "order",
            },
        )
        book.append(
            "invoice",
            "channels",
            {
                "client": cid,
                "channel": CHANNEL,
                "amount": offer["price"],
                "currency": offer["currency"],
                "text": f"Счёт за {oid}: {price}.",
            },
            offer=oid,
            product=offer["product"],
        )
    logger.info("[SHOP] order %s for %s", cid, oid)
    return _to_order(token)


@shop_bp.route("/order/signal", methods=["POST"])
def order_signal() -> Response:
    """One of the two fixed signals of an order's page; each is written once per order."""
    form = _form()
    if form is None:
        return _refuse("bad_form")
    token = form.get("token")
    # the secret's shape is looked at before the ledger is
    if not isinstance(token, str) or not _TOKEN.fullmatch(token):
        return _refuse("no_order")
    st = _state()
    if _order(st, token) is None:
        return _refuse("no_order")
    if not st.channels.get(CHANNEL, {}).get("enabled"):
        return _refuse("closed")
    signal = form.get("signal")
    if signal not in SIGNALS:
        return _refuse("bad_form")
    # one at a time, on the ledger as it is then: two posts of one signal write it once
    with _SIGNAL_LOCK:
        found = _order(_state(), token)
        if found is None:
            return _refuse("no_order")
        cid, client, order = found
        repeated = signal in order["signals"] or (
            signal == "paid" and order["product"] in client["paid"]
        )
        if not repeated:
            said, answer = SIGNALS[signal]
            book = journal.ledger()
            with journal.embedding_deferred():
                book.append(
                    "client.message",
                    "channels",
                    {"client": cid, "channel": CHANNEL, "signal": signal, "text": said},
                    offer=order["offer"],
                    product=order["product"],
                    project=order["project"],
                )
                book.append(
                    "client.reply",
                    "channels",
                    {"client": cid, "channel": CHANNEL, "text": answer, "auto": signal},
                )
            logger.info("[SHOP] signal %s %s", cid, signal)
    return _to_order(token)


@shop_bp.route("/order")
def order_page() -> Response:
    token = request.args.get("token")
    # the secret's shape is looked at before the ledger is
    if not isinstance(token, str) or not _TOKEN.fullmatch(token):
        return _refuse("no_order")
    st = _state()
    found = _order(st, token)
    if found is None:
        return _refuse("no_order")
    cid, client, order = found
    pid = order["product"]
    product = st.products.get(pid or "")
    title = (product["title"] if product is not None else "") or str(pid or "")
    price = _price(order["amount"], order["currency"])
    signals = order["signals"]
    paid = pid in client["paid"]
    delivered_at = next(
        (d["at"] for d in st.deliveries if d["client"] == cid and d["product"] == pid),
        None,
    )
    open_ = bool(st.channels.get(CHANNEL, {}).get("enabled"))
    # read now, never from the ledger: an order taken before the owner set the text
    # shows it as soon as it is set
    payment_text = _payment_text()
    if paid and delivered_at:
        state = f"Оплата подтверждена, файл выдан {_when(delivered_at)}. Его можно скачать ещё раз."
    elif paid:
        state = "Оплата подтверждена. Спасибо за покупку!"
    elif "paid" in signals:
        state = f"Вы сообщили об оплате {_when(signals['paid'])}. Продавец проверяет поступление денег; ссылка для скачивания появится на этой странице."
    elif payment_text:
        state = "Заказ принят, счёт выставлен. Оплатите его и нажмите «Я оплатил(а)»."
    else:
        # no payment text yet: nothing can be paid by this page, so it shows no
        # «Как оплатить» and no «Я оплатил(а)»
        state = ACK_ORDER_WAIT
    address = f"/shop/order?token={token}"
    return _page(
        "order",
        title=f"Заказ {cid}",
        cid=cid,
        product_title=title,
        price=price,
        state=state,
        invoice=not paid,
        payment_text=payment_text,
        open=open_,
        can_pay=open_ and not paid and "paid" not in signals and bool(payment_text),
        can_help=open_ and "help" not in signals,
        help_at=_when(signals["help"]) if "help" in signals else "",
        download_href=f"/shop/order/download?token={token}" if paid else "",
        token=token,
        self_href=address,
        # built from the constant, never from the request's headers
        mail_href="mailto:?subject="
        + quote(f"Заказ {cid}", safe="")
        + "&body="
        + quote(f"https://{SHOP_HOST}{address}", safe=""),
        refresh=not (paid and delivered_at),
        messages=[
            {
                "who": "Вы" if message["from"] == "client" else "Продавец",
                "at": _when(message["at"]),
                "text": message["text"],
            }
            for message in client["messages"]
        ],
    )


# ----- the download -----------------------------------------------------------------------------


@shop_bp.route("/order/download")
def order_download() -> Response:
    """The paid product: the archive its descriptor lists, or its one registered file.
    The delivery is this download; its record is written once per buyer and product.
    HEAD answers with the status and the type GET would, builds and records nothing."""
    token = request.args.get("token")
    # the secret's shape is looked at before the ledger is
    if not isinstance(token, str) or not _TOKEN.fullmatch(token):
        return _refuse("no_order")
    st = _state()
    found = _order(st, token)
    if found is None:
        return _refuse("no_order")
    cid, client, order = found
    pid = order["product"]
    if pid not in client["paid"]:
        return _refuse("not_paid")
    product = st.products.get(pid or "")
    if product is None:
        return _refuse("not_found")
    try:
        # the client is known and no text is passed: the only refusal is a channel
        # that is off
        channels.gate(st, CHANNEL, "deliver", client=cid, product=pid)
    except channels.GateRefused:
        return _refuse("paused")
    placed = _placed(product)
    if placed is None:
        return _refuse("no_delivery")
    showcase, problems = _showcase(product, placed)
    if problems:
        return _refuse("no_delivery")
    head = request.method == "HEAD"
    packed: Any = None  # the archive as an open temporary file
    if showcase is None:
        # no descriptor: the one registered file, as on the other channels
        where, count = placed[1], 1
        try:
            size = where.stat().st_size
        except OSError:
            return _refuse("no_delivery")
    elif head:
        return _private(Response(mimetype="application/zip"))
    else:
        try:
            packed, size = _archive(showcase)
        except (OSError, ValueError) as exc:
            logger.error("[SHOP] the archive of %s was not built: %s", pid, exc)
            return _message("no_delivery")
        where, count = showcase.root, len(showcase.archive_files)
    try:
        if not head:
            # the record is written before the bytes leave, once per (client, product)
            with _DELIVERY_LOCK:
                st = _state()
                if not any(
                    d["client"] == cid and d["product"] == pid for d in st.deliveries
                ):
                    with journal.embedding_deferred():
                        journal.ledger().append(
                            "delivery",
                            "channels",
                            {
                                "client": cid,
                                "channel": CHANNEL,
                                "path": str(where),
                                "files": count,
                            },
                            product=pid,
                            project=product["project"],
                        )
            logger.info("[SHOP] download %s %s %d", cid, pid, size)
        if packed is None:
            return _private(send_file(str(where), as_attachment=True))
        response = send_file(
            packed,
            mimetype="application/zip",
            as_attachment=True,
            download_name=f"{showcase.archive_name}.zip",
            conditional=False,
            etag=False,
        )
        # send_file cannot size an open file
        response.content_length = size
        response.call_on_close(packed.close)
        return _private(response)
    except Exception:
        if packed is not None:
            packed.close()
        raise


def _archive(showcase: Showcase) -> tuple[Any, int]:
    """The product's archive, written to a temporary file on disk: (the open file at
    its start, its size). OSError or ValueError when a listed file cannot be packed."""
    packed = tempfile.TemporaryFile()
    try:
        with zipfile.ZipFile(
            packed, "w", zipfile.ZIP_DEFLATED, strict_timestamps=False
        ) as archive:
            for name in showcase.archive_files:
                source = _inside(showcase.root, name)
                if source is None:
                    raise FileNotFoundError(name)
                archive.write(source, arcname=f"{showcase.archive_name}/{name}")
        size = packed.seek(0, os.SEEK_END)
        packed.seek(0)
    except BaseException:
        packed.close()
        raise
    return packed, size


# ----- the demo -----------------------------------------------------------------------------------


@shop_bp.route("/demo/<product>")
def demo_redirect(product: str) -> Response:
    """The catch-all below would take the router's own slash redirect, so it is here."""
    if not _PRODUCT.fullmatch(product):
        return _refuse("not_found")
    return redirect(f"/shop/demo/{product}/", code=308)


@shop_bp.route("/demo/<product>/", defaults={"name": ""})
@shop_bp.route("/demo/<product>/<path:name>")
def demo_file(product: str, name: str) -> Response:
    """One file of a product's demo: exactly a name its descriptor lists, of a product
    on sale. The address is never joined into a path before that. A demo file is not
    given out as a service worker's script (a request with a ``Service-Worker``
    header), and the shop sends no ``Service-Worker-Allowed``."""
    if not _PRODUCT.fullmatch(product):
        return _refuse("not_found")
    if request.headers.get("Service-Worker") is not None:
        return _refuse("not_found")
    item = next(
        (i for i in _shelf(_state()) if i["product"]["id"] == product), None
    )
    showcase = item["showcase"] if item is not None else None
    if showcase is None or not showcase.demo_entry:
        return _refuse("not_found")
    name = name or showcase.demo_entry
    if name not in showcase.demo_files:
        return _refuse("not_found")
    source = _inside(showcase.root, showcase.demo_overrides.get(name, name))
    if source is None:
        return _refuse("not_found")
    response = send_file(
        str(source),
        mimetype=_TYPES.get(Path(name).suffix.lower(), "application/octet-stream"),
    )
    response.headers["X-Robots-Tag"] = NO_INDEX
    # the shop's policy always; the product's own beside it, never instead of it
    response.headers["Content-Security-Policy"] = DEMO_CSP
    if showcase.demo_csp:
        response.headers.add("Content-Security-Policy", showcase.demo_csp)
    return response


# ----- everything else under /shop/ ---------------------------------------------------------------


@shop_bp.route("/", methods=["POST"], defaults={"rest": ""})
@shop_bp.route("/<path:rest>", methods=["GET", "POST"])
def not_found(rest: str) -> Response:
    return _refuse("not_found")
